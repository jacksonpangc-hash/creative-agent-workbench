#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::{io::{Read, Write}, net::{TcpListener, TcpStream}, process::{Child,Command,Stdio}, sync::Mutex, time::Duration};
use tauri::Manager;
struct Worker(Mutex<Option<Child>>);
fn stop_worker(child: &mut Child) {
    if matches!(child.try_wait(), Ok(Some(_))) { return }
    // PyInstaller onefile forks a worker. Killing only its bootloader leaves
    // that child serving after the desktop exits. The group is created solely
    // for this owned worker; never signal a shared shell/browser process group.
    #[cfg(unix)] {
        let group = format!("-{}", child.id());
        let _ = Command::new("/bin/kill").args(["-TERM", &group])
            .stdout(Stdio::null()).stderr(Stdio::null()).status();
        for _ in 0..50 {
            if matches!(child.try_wait(), Ok(Some(_))) { return }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = Command::new("/bin/kill").args(["-KILL", &group])
            .stdout(Stdio::null()).stderr(Stdio::null()).status();
    }
    #[cfg(not(unix))] { let _ = child.kill(); }
    let _ = child.wait();
}
fn main() {
    let app = tauri::Builder::default().setup(|app| {
        // The OS chooses the port. The Python side binds loopback and authenticates every API route.
        let listener=TcpListener::bind("127.0.0.1:0")?;
        let port=listener.local_addr()?.port(); drop(listener);
        let resources=app.path().resource_dir()?;
        let exe=std::env::current_exe()?;
        let worker_name=if cfg!(windows){"creative-worker.exe"}else{"creative-worker"};
        let worker=exe.parent().unwrap().join(worker_name);
        let worker=if worker.exists(){worker}else{resources.join(worker_name)};
        // Refresh per-user paths before starting the server. No developer ID is
        // opted in automatically; an internal install must explicitly register it.
        let bridge_name=if cfg!(windows){"creative-browser-host.exe"}else{"creative-browser-host"};
        let bridge=exe.parent().unwrap().join(bridge_name);
        let bridge=if bridge.exists(){bridge}else{resources.join(bridge_name)};
        if bridge.is_file() {
            let _=Command::new(&worker).args(["browser-host-register","--refresh-existing","--host"])
                .arg(&bridge).arg("--worker").arg(&worker).arg("--desktop").arg(&exe).arg("--desktop-pid").arg(std::process::id().to_string())
                .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status();
        }
        let token = {
            // OS CSPRNG, passed through stdin instead of process arguments.
            let mut bytes=[0u8;32];
            getrandom_fill(&mut bytes)?;
            bytes.iter().map(|b|format!("{:02x}",b)).collect::<String>()
        };
        let mut command=Command::new(&worker);
        command.args(["serve","--no-open","--token-stdin","--port",&port.to_string(),"--ui-dir"])
            .arg(resources.join("ui")).stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null());
        #[cfg(unix)] { use std::os::unix::process::CommandExt; command.process_group(0); }
        let mut child=command.spawn()?;
        child.stdin.take().unwrap().write_all(format!("{}\n",token).as_bytes())?;
        // Do not navigate until our authenticated worker responds, or open a different local service.
        let mut ready=false;
        for _ in 0..1200 {
            if child.try_wait()?.is_some(){break}
            if let Ok(mut stream)=TcpStream::connect_timeout(&format!("127.0.0.1:{}",port).parse()?,Duration::from_millis(100)) {
                stream.set_read_timeout(Some(Duration::from_millis(150)))?;
                let req=format!("GET /api/projects HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n",port,token);
                if stream.write_all(req.as_bytes()).is_ok() { let mut buf=[0u8;80]; if let Ok(n)=stream.read(&mut buf){if String::from_utf8_lossy(&buf[..n]).starts_with("HTTP/1.1 200"){ready=true;break}} }
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        if !ready {stop_worker(&mut child);return Err("本地工作进程未能启动；请检查应用安装与系统密钥库".into())}
        app.manage(Worker(Mutex::new(Some(child))));
        let url=url::Url::parse(&format!("http://127.0.0.1:{}/#token={}",port,token))?;
        tauri::WebviewWindowBuilder::new(app,"main",tauri::WebviewUrl::External(url))
            .title("Creative Agent · 创意策略工作台").inner_size(1440.0,940.0).min_inner_size(720.0,600.0).build()?;
        Ok(())
    }).build(tauri::generate_context!()).expect("无法启动 Creative Agent");
    app.run(|handle,event| {if let tauri::RunEvent::Exit=event {if let Some(state)=handle.try_state::<Worker>() {if let Some(mut child)=state.0.lock().unwrap().take(){stop_worker(&mut child);}}}});
}
fn getrandom_fill(buf:&mut[u8])->Result<(),Box<dyn std::error::Error>> {
    getrandom::fill(buf).map_err(|e|format!("OS random source unavailable: {}",e).into())
}
