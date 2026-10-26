const LABELS={'workspace.use':'工作空间','product.manage':'商品管理','assets.import':'素材导入','assets.analyze':'素材分析','discovery.public':'公开素材发现','discovery.paid':'商业数据查询','video.breakdown':'视频拆解','creative.generate':'创意分析','script.generate':'脚本创作','script.export':'脚本导出','providers.configure':'模型与数据配置'};

export function mountAdmin(root, fetcher=globalThis.fetch.bind(globalThis)) {
  const doc=root.ownerDocument;
  let token='', licenses=[], config={features:[],period_days:[]}, selected='', pending=false;
  let feedback=null;
  const el=(tag,text,attrs={})=>{
    const node=doc.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);
    for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));
    return node;
  };
  const notice=(text,error=false)=>{feedback.textContent=text;feedback.setAttribute('role',error?'alert':'status')};
  const field=(form,name,label,type='text',value='')=>{
    const wrap=el('label',label);const input=el('input',null,{name,type,required:'',autocomplete:type==='password'?'off':'on'});
    input.value=value;wrap.append(input);form.append(wrap);return input;
  };
  const periods=(form,name,label)=>{
    const wrap=el('label',label),select=el('select',null,{name});
    config.period_days.forEach(day=>{const option=el('option',`${day} 天`,{value:day});select.append(option)});
    select.value='30';wrap.append(select);form.append(wrap);return select;
  };
  const button=(text,action,handler)=>{const node=el('button',text,{type:'button','data-action':action});node.addEventListener('click',handler);return node};
  async function api(path,body){
    const headers={};if(token)headers.Authorization='Bearer '+token;
    if(body!==undefined)headers['Content-Type']='application/json';
    const response=await fetcher(path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body),credentials:'omit',cache:'no-store'});
    const data=await response.json();
    if(!response.ok){if(response.status===401&&path!=='/admin/login'){token='';render()}throw new Error(typeof data.detail==='string'?data.detail:'操作未完成，请重试')}
    return data;
  }
  async function work(action){
    if(pending)return;pending=true;
    root.querySelectorAll('button').forEach(node=>node.disabled=true);
    try{await action()}catch(error){notice(error.message||'连接失败，请重试',true)}
    finally{pending=false;root.querySelectorAll('button').forEach(node=>node.disabled=false)}
  }
  async function reload(){licenses=await api('/admin/licenses');render()}
  async function manage(action,body={}){
    return work(async()=>{await api('/admin/licenses/'+encodeURIComponent(selected)+'/'+action,body);await reload();notice(action==='features'?'开放功能已更新。客户需重新登录；旧离线租约最长在24小时内到期。':'操作已保存。停用、迁移及释放对已离线设备最迟在租约到期时生效。')});
  }
  function render(){
    const header=el('header');header.append(el('p','创意工作台'),el('h1','授权管理'));
    feedback=el('p','',{role:'status',class:'feedback'});root.replaceChildren(header,feedback);
    if(!token){
      const card=el('section',null,{class:'login-card'});card.append(el('h2','管理员登录'),el('p','使用部署时设置的管理员密码。登录状态仅保存在当前页面，刷新页面需重新登录。'));
      const form=el('form',null,{'data-form':'login'});const password=field(form,'admin_password','管理员密码','password');
      const otp=field(form,'admin_otp','验证器验证码');otp.required=false;otp.maxLength=6;otp.inputMode='numeric';otp.autocomplete='one-time-code';
      form.append(el('p','正式授权服务需要填写验证器中的六位验证码。'));
      password.maxLength=512;form.append(el('button','登录',{type:'submit'}));
      form.addEventListener('submit',event=>{event.preventDefault();const secret=password.value,code=otp.value;password.value='';otp.value='';void work(async()=>{
        const result=await api('/admin/login',{password:secret,otp:code});token=result.token;
        config=await api('/admin/config');await reload();
      })});card.append(form);root.append(card);return;
    }
    header.append(button('退出登录','logout',()=>void work(async()=>{try{await api('/admin/logout',{})}finally{token='';licenses=[];selected='';render()}})));
    const layout=el('div',null,{class:'layout'}),issue=el('section',null,{class:'panel'});
    issue.append(el('h2','开通客户账号'),el('p','套餐从客户首次激活起算。授权编号将在开通后自动生成。'));
    const form=el('form',null,{'data-form':'issue'}),customer=field(form,'customer','客户名称');customer.maxLength=200;
    const password=field(form,'customer_password','客户授权密码','password');password.minLength=12;password.maxLength=512;
    const days=periods(form,'days','套餐时长');const features=el('fieldset');features.append(el('legend','开放功能'));
    config.features.forEach(feature=>{const label=el('label',null,{class:'check'}),input=el('input',null,{type:'checkbox',name:'features',value:feature});input.checked=true;label.append(input,doc.createTextNode(LABELS[feature]||feature));features.append(label)});
    form.append(features,el('button','开通账号',{type:'submit'}));
    form.addEventListener('submit',event=>{event.preventDefault();const body={customer:customer.value.trim(),password:password.value,days:Number(days.value),features:[...features.querySelectorAll('input:checked')].map(node=>node.value)};password.value='';void work(async()=>{
      const item=await api('/admin/licenses',body);selected=item.id;await reload();notice('已开通账号，授权编号：'+item.id+'。请通过安全渠道将编号及刚设置的密码交给客户。');
    })});issue.append(form);layout.append(issue);
    const accounts=el('section',null,{class:'panel accounts'});accounts.append(el('h2','客户授权'),button('刷新列表','reload',()=>void work(reload)));
    if(!licenses.length)accounts.append(el('p','尚未开通账号。'));
    licenses.forEach(item=>{
      const row=el('article',null,{class:'account'}),state=item.disabled?'已停用':!item.activated_at?'未激活':item.expires_at*1000<=Date.now()?'已到期':'使用中';
      row.append(el('h3',item.customer),el('p',state+' · '+(item.expires_at?new Date(item.expires_at*1000).toLocaleString('zh-CN'):'首次激活后起算')),el('code',item.id));
      const open=button('管理此账号','select',()=>{selected=item.id;render()});open.setAttribute('data-license',item.id);row.append(open);accounts.append(row);
    });layout.append(accounts);root.append(layout);
    const item=licenses.find(value=>value.id===selected);if(!item)return;
    const detail=el('section',null,{class:'panel detail'});detail.append(el('h2','管理 '+item.customer),el('p','授权编号：'+item.id),el('p','设备编号：'+(item.device_id||'未绑定')));
    const featureForm=el('form',null,{'data-form':'features'}),featureSet=el('fieldset');featureSet.append(el('legend','调整开放功能'));
    config.features.forEach(feature=>{const label=el('label',null,{class:'check'}),input=el('input',null,{type:'checkbox',name:'account_features',value:feature});input.checked=item.features.includes(feature);label.append(input,doc.createTextNode(LABELS[feature]||feature));featureSet.append(label)});
    featureForm.append(featureSet,el('button','保存开放功能',{type:'submit'}));
    featureForm.addEventListener('submit',event=>{event.preventDefault();void manage('features',{features:[...featureSet.querySelectorAll('input:checked')].map(node=>node.value)})});
    detail.append(featureForm,el('p','功能调整保留原到期时间和停用状态。客户需重新登录后取得新的权限；旧离线租约可能继续有效至最长24小时。'));
    const renewal=el('form',null,{'data-form':'renew',class:'inline-form'}),renewDays=periods(renewal,'renew_days','续费时长');renewal.append(el('button','续费',{type:'submit'}));
    renewal.addEventListener('submit',event=>{event.preventDefault();void manage('renew',{days:Number(renewDays.value)})});detail.append(renewal);
    detail.append(button(item.disabled?'恢复使用':'停用授权',item.disabled?'enable':'disable',()=>void manage(item.disabled?'enable':'disable')));
    const transfer=el('form',null,{'data-form':'transfer',class:'inline-form'}),device=field(transfer,'device_id','新设备编号');device.maxLength=200;
    transfer.append(el('button','迁移到新设备',{type:'submit'}));transfer.addEventListener('submit',event=>{event.preventDefault();void manage('transfer',{device_id:device.value.trim()})});detail.append(transfer);
    detail.append(button('释放当前设备','release',()=>void manage('release')),el('p','续费不会自动恢复已停用的账号。恢复使用后，客户需重新登录；到期时间不变。旧设备离线时，停用或换机最多需要 24 小时生效。'));
    const audit=el('div',null,{'data-audit-container':''});
    detail.append(button('查看操作记录','audit',()=>void work(async()=>{
      const rows=await api('/admin/licenses/'+encodeURIComponent(item.id)+'/audit');audit.replaceChildren();
      const list=el('ol',null,{'data-audit':''});
      const actions={issue:'开通',activate:'首次激活',login:'客户登录',renew:'续期',disable:'停用',enable:'恢复',release:'客户释放设备',admin_release:'管理员释放设备',transfer:'换机',set_features:'功能调整'};
      rows.forEach(row=>{const entry=el('li',new Date(row.at*1000).toLocaleString('zh-CN')+' · '+(actions[row.action]||row.action));
        if(row.action==='set_features'){let data={};try{data=JSON.parse(row.detail)}catch{}if(Array.isArray(data.features))entry.append(el('p','调整为：'+data.features.map(feature=>LABELS[feature]||feature).join('、')))}list.append(entry)});
      audit.append(list);if(!rows.length)audit.append(el('p','尚无操作记录。'));
    })),audit);root.append(detail);
  }
  render();
}
if(typeof document!=='undefined'&&document.getElementById('app'))mountAdmin(document.getElementById('app'));
