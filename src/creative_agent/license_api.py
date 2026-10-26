"""Local routes: protected by the same session bearer as the desktop API."""
import hmac
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from .commercial_license import LicenseError

class LicenseLogin(BaseModel):
    model_config=ConfigDict(extra='forbid')
    license_id: str=Field(min_length=1,max_length=100)
    password: SecretStr=Field(min_length=1,max_length=512)


class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handler(request):
            try: return await original(request)
            except RequestValidationError as exc:
                raise HTTPException(422,'授权编号或密码格式无效') from exc
        return handler

def register_license(app, runtime):
    app.state.license_runtime=runtime
    def authorize(request:Request):
        if getattr(request.state,'team_authorized',False) is True: return
        bearer=getattr(app.state,'token',None)
        if not bearer or not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+bearer):
            raise HTTPException(401,'此会话未授权')
    router=APIRouter(prefix='/api/license',dependencies=[Depends(authorize)],route_class=PrivateValidationRoute)
    @router.get('/status')
    def status(): return runtime.status()
    @router.post('/login')
    def login(body:LicenseLogin):
        if not hasattr(runtime,'login'): raise HTTPException(409,'当前为内部开发模式，未配置商业授权服务')
        try: return runtime.login(body.license_id,body.password.get_secret_value())
        except (LicenseError,ValueError) as exc: raise HTTPException(403,str(exc)) from exc
    @router.post('/refresh')
    def refresh():
        if not hasattr(runtime,'refresh'): raise HTTPException(409,'当前为内部开发模式，未配置商业授权服务')
        try: return runtime.refresh()
        except (LicenseError,ValueError) as exc: raise HTTPException(403,str(exc)) from exc
    @router.post('/release')
    def release():
        if not hasattr(runtime,'release'): raise HTTPException(409,'当前为内部开发模式，未配置商业授权服务')
        try: return runtime.release()
        except (LicenseError,ValueError) as exc: raise HTTPException(403,str(exc)) from exc
    app.include_router(router)
    return runtime
