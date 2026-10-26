//! Chrome/Edge Native Messaging host. stdout contains framed JSON only.
use std::{io::{self, Read, Write}, net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream}, path::{Path,PathBuf}, time::{Duration, Instant}};
use sha2::{Digest,Sha256};
use zeroize::Zeroizing;
use serde_json::{json, Value};
const LIMIT: usize = 256 * 1024;
const OPERATIONS: [&str;6] = ["get_status","list_allowed_projects","save_references","get_import_receipt","open_workbench","revoke_connection"];
fn data_dir() -> PathBuf {
    if let Some(path)=std::env::var_os("CREATIVE_DATA_DIR") {return PathBuf::from(path)}
    if cfg!(windows) {return PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_default()).join("CreativeAgent")}
    let home=PathBuf::from(std::env::var_os("HOME").unwrap_or_default());
    if cfg!(target_os="macos") {home.join("Library/Application Support/CreativeAgent")}
    else {std::env::var_os("XDG_DATA_HOME").map(PathBuf::from).unwrap_or_else(||home.join(".local/share")).join("creative-agent")}
}
fn read_frame(input:&mut impl Read)->io::Result<Option<Value>> {
    let mut header=[0u8;4];
    let n=input.read(&mut header[..1])?;
    if n==0 {return Ok(None)}
    input.read_exact(&mut header[1..])?;
    let len=u32::from_le_bytes(header) as usize;
    if len==0 || len>LIMIT {return Err(io::Error::new(io::ErrorKind::InvalidData,"message size"))}
    let mut bytes=vec![0;len];input.read_exact(&mut bytes)?;
    serde_json::from_slice(&bytes).map(Some).map_err(|_|io::Error::new(io::ErrorKind::InvalidData,"invalid JSON"))
}
fn write_frame(output:&mut impl Write,value:&Value)->io::Result<()> {
    let data=serde_json::to_vec(value)?;
    if data.len()>LIMIT {return Err(io::Error::new(io::ErrorKind::InvalidData,"response size"))}
    output.write_all(&(data.len() as u32).to_le_bytes())?;output.write_all(&data)?;output.flush()
}
const SERVICE: &str = "creative-material-agent";
fn bridge_secret_ref(resolved_root:&str)->String {
    // Registration writes Python Path.resolve() as this exact Unicode string.
    // Do not canonicalize again on Windows: that adds a \\?\ prefix which Python
    // did not hash. No lossy conversion or Unicode normalization is allowed.
    let digest=Sha256::digest(resolved_root.as_bytes());
    let suffix=digest[..12].iter().map(|b|format!("{:02x}",b)).collect::<String>();
    format!("browser-native-bridge-v1-{}",suffix)
}
#[cfg(target_os="macos")]
fn read_bridge_key(reference:&str)->Result<Zeroizing<String>,()> {
    let entry=keyring::Entry::new(SERVICE,reference).map_err(|_|())?;
    entry.get_password().map(Zeroizing::new).map_err(|_|())
}
#[cfg(windows)]
fn read_bridge_key(reference:&str)->Result<Zeroizing<String>,()> {
    // python-keyring WinVault stores the latest credential at SERVICE; previous
    // accounts move to username@SERVICE. keyring-rs defaults to username.SERVICE,
    // so its default target must NOT be used here.
    for target in [SERVICE.to_string(),format!("{}@{}",reference,SERVICE)] {
        let entry=keyring::Entry::new_with_target(&target,SERVICE,reference).map_err(|_|())?;
        let matches=|entry:&keyring::Entry|entry.get_attributes().ok().and_then(|attrs|attrs.get("username").cloned()).as_deref()==Some(reference);
        if !matches(&entry){continue}
        if let Ok(value)=entry.get_password(){
            let value=Zeroizing::new(value);
            // Another Python write can replace SERVICE between reads; never use
            // a model/provider credential that belongs to a different account.
            if matches(&entry){return Ok(value)}
        }
    }
    Err(())
}
#[cfg(not(any(target_os="macos",windows)))]
fn read_bridge_key(_reference:&str)->Result<Zeroizing<String>,()> {Err(())}
fn bounded_bridge_key(reference:String)->Result<Zeroizing<String>,()> {
    // Native keychain access may wait for the OS. Bound this request without a
    // plaintext fallback, and erase any late result when its receiver is gone.
    let (send,receive)=std::sync::mpsc::sync_channel(1);
    std::thread::spawn(move||{let _=send.send(read_bridge_key(&reference));});
    // First use can require a macOS Keychain confirmation. Three seconds made
    // that dialog disappear before a person could act. Remain bounded while
    // allowing the ordinary OS permission flow; never fall back to plaintext.
    receive.recv_timeout(Duration::from_secs(45)).map_err(|_|())?
}
fn remaining(deadline:Instant)->io::Result<Duration>{
    let duration=deadline.saturating_duration_since(Instant::now());
    if duration.is_zero(){Err(io::Error::new(io::ErrorKind::TimedOut,"bridge timeout"))}else{Ok(duration)}
}
fn http_response_head(bytes:&[u8])->io::Result<(u16,usize)>{
    let invalid=||io::Error::new(io::ErrorKind::InvalidData,"invalid local response");
    let text=std::str::from_utf8(bytes).map_err(|_|invalid())?;
    let mut lines=text.split("\r\n");let mut status_line=lines.next().unwrap_or("").split_whitespace();
    if status_line.next()!=Some("HTTP/1.1"){return Err(invalid())}
    let status=status_line.next().and_then(|value|value.parse::<u16>().ok()).ok_or_else(invalid)?;
    if !(200..600).contains(&status)||(300..400).contains(&status){return Err(invalid())}
    let mut length=None;
    for line in lines {
        let (name,value)=line.split_once(':').ok_or_else(invalid)?;
        if name.eq_ignore_ascii_case("transfer-encoding")||name.eq_ignore_ascii_case("content-encoding"){return Err(invalid())}
        if name.eq_ignore_ascii_case("content-length"){
            if length.is_some(){return Err(invalid())}
            length=Some(value.trim().parse::<usize>().map_err(|_|invalid())?);
        }
    }
    let length=length.ok_or_else(invalid)?;
    if length>LIMIT{return Err(invalid())}
    Ok((status,length))
}
fn post_rpc(port:u16,origin:&str,request:&Value,key:&str)->io::Result<Value>{
    if port<1024||key.len()<32||key.len()>128||!key.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'-'||b==b'_'){
        return Err(io::Error::new(io::ErrorKind::InvalidInput,"invalid bridge configuration"))
    }
    let body=serde_json::to_vec(&json!({"origin":origin,"request":request}))?;
    if body.len()>LIMIT{return Err(io::Error::new(io::ErrorKind::InvalidInput,"message size"))}
    let deadline=Instant::now()+Duration::from_secs(12);
    // Numeric loopback only: no DNS, proxy variables, redirects or remote URLs.
    let address=SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::LOCALHOST,port));
    let mut stream=TcpStream::connect_timeout(&address,Duration::from_secs(2))?;
    stream.set_write_timeout(Some(remaining(deadline)?))?;
    let headers=Zeroizing::new(format!("POST /browser-bridge/rpc HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nContent-Type: application/json\r\nAccept: application/json\r\nX-Creative-Bridge-Key: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",port,key,body.len()));
    stream.write_all(headers.as_bytes())?;stream.write_all(&body)?;
    let mut buffer=Vec::new();let mut chunk=[0u8;4096];
    let header_end=loop{
        if let Some(at)=buffer.windows(4).position(|part|part==b"\r\n\r\n"){break at}
        if buffer.len()>16384{return Err(io::Error::new(io::ErrorKind::InvalidData,"response headers too large"))}
        stream.set_read_timeout(Some(remaining(deadline)?))?;
        let n=stream.read(&mut chunk)?;
        if n==0{return Err(io::Error::new(io::ErrorKind::UnexpectedEof,"response incomplete"))}
        buffer.extend_from_slice(&chunk[..n]);
    };
    if header_end>16384{return Err(io::Error::new(io::ErrorKind::InvalidData,"response headers too large"))}
    let (status,length)=http_response_head(&buffer[..header_end])?;
    let mut data=buffer.split_off(header_end+4);
    if data.len()>length{return Err(io::Error::new(io::ErrorKind::InvalidData,"response size"))}
    while data.len()<length{
        stream.set_read_timeout(Some(remaining(deadline)?))?;
        let maximum=(length-data.len()).min(chunk.len());
        let n=stream.read(&mut chunk[..maximum])?;
        if n==0{return Err(io::Error::new(io::ErrorKind::UnexpectedEof,"response incomplete"))}
        data.extend_from_slice(&chunk[..n]);
    }
    let result=serde_json::from_slice::<Value>(&data)?;
    if result["ok"].is_boolean(){return Ok(result)}
    let (code,message)=match status{
        401=>("bridge_unauthorized","收藏连接已变更，请重新打开桌面应用"),
        403=>("license_required","当前许可未开放收藏，请在桌面检查授权"),
        404=>("update_required","桌面应用不支持当前收藏协议，请更新后重试"),
        413=>("message_too_large","一次收藏内容过多，请分批保存"),
        _=>("bridge_unavailable","本机应用未返回有效回执，请查询本次回执后重试")
    };
    Ok(json!({"ok":false,"code":code,"message":message}))
}
fn launch_desktop(config:&Value)->bool{
    let desktop=match config["desktop_path"].as_str(){Some(path)=>Path::new(path),None=>return false};
    if !desktop.is_file(){return false}
    #[cfg(target_os="macos")]{
        let bundle=desktop.ancestors().find(|p|p.extension().and_then(|x|x.to_str())==Some("app"));
        if let Some(bundle)=bundle{return std::process::Command::new("open").arg("-a").arg(bundle).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn().is_ok()}
    }
    #[cfg(windows)]{
        use std::os::windows::process::CommandExt;
        return std::process::Command::new(desktop).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).creation_flags(0x08000000).spawn().is_ok()
    }
    false
}
fn forward(config:&Value,origin:&str,request:&Value)->Value {
    let failure=|code:&str,message:&str|json!({"ok":false,"code":code,"message":message});
    if !OPERATIONS.contains(&request["operation"].as_str().unwrap_or("")){return failure("operation_denied","收藏工具不支持此操作")}
    let root=match config["data_dir"].as_str(){Some(root) if Path::new(root).is_absolute()=>root,_=>return failure("bridge_unavailable","请修复桌面应用安装")};
    let endpoint=std::fs::read(Path::new(root).join("native-endpoint.json")).ok().and_then(|bytes|serde_json::from_slice::<Value>(&bytes).ok());
    let port=endpoint.as_ref().and_then(|v|v["port"].as_u64()).filter(|p|(1024..=65535).contains(p));
    if port.is_none(){
        if request["operation"]=="open_workbench"&&launch_desktop(config){return json!({"ok":true,"state":"desktop_starting","message":"正在打开创意工作台，请稍后刷新连接"})}
        return failure("desktop_offline","桌面应用尚未启动，请打开创意工作台后重试")
    }
    let key=match bounded_bridge_key(bridge_secret_ref(root)){Ok(key)=>key,Err(_)=>return failure("keyring_unavailable","系统密钥库无法读取收藏连接，请在桌面设置中检查连接")};
    match post_rpc(port.unwrap() as u16,origin,request,&key){
        Ok(result)=>result,
        Err(error) if matches!(error.kind(),io::ErrorKind::TimedOut|io::ErrorKind::WouldBlock)=>failure("bridge_timeout","保存仍未确认，请查询本次回执或使用同一请求重试"),
        Err(error) if matches!(error.kind(),io::ErrorKind::ConnectionRefused|io::ErrorKind::NotConnected|io::ErrorKind::ConnectionReset|io::ErrorKind::ConnectionAborted)=>{
            if request["operation"]=="open_workbench"&&launch_desktop(config){json!({"ok":true,"state":"desktop_starting","message":"正在打开创意工作台，请稍后刷新连接"})}
            else {failure("desktop_offline","桌面应用尚未启动，请打开创意工作台后重试")}
        },
        Err(_)=>failure("bridge_unavailable","本机应用未返回有效回执，请查询本次回执后重试")
    }
}
fn main() {
    let config=std::fs::read(data_dir().join("native-host.json")).ok().and_then(|raw|serde_json::from_slice::<Value>(&raw).ok());
    let origin=std::env::args().skip(1).find(|arg|arg.starts_with("chrome-extension://"));
    let allowed=config.as_ref().and_then(|v|v["allowed_origins"].as_array()).zip(origin.as_ref()).map(|(origins,origin)|origins.iter().any(|x|x.as_str()==Some(origin.as_str()))).unwrap_or(false);
    let mut input=io::stdin().lock();let mut output=io::stdout().lock();
    if !allowed {let _=write_frame(&mut output,&json!({"ok":false,"code":"origin_denied","message":"收藏扩展来源未获准，请检查安装版本"}));return}
    while let Ok(Some(request))=read_frame(&mut input){
        let response=forward(config.as_ref().unwrap(),origin.as_ref().unwrap(),&request);
        if write_frame(&mut output,&response).is_err(){break}
    }
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn framing_round_trip_and_limits(){let value=json!({"operation":"get_status","title":"中文"});let mut bytes=Vec::new();write_frame(&mut bytes,&value).unwrap();assert_eq!(read_frame(&mut bytes.as_slice()).unwrap(),Some(value));assert!(read_frame(&mut (300000u32.to_le_bytes().as_slice())).is_err());}
    #[test] fn reference_matches_python_resolved_root_hash(){
        assert_eq!(bridge_secret_ref("/tmp/creative-bridge-test"),"browser-native-bridge-v1-d653c8671df80aca35757d55");
        assert_eq!(bridge_secret_ref("/Users/example/Library/Application Support/CreativeAgent"),"browser-native-bridge-v1-34a011f08fea07fc83a97fa4");
    }
    #[test] fn direct_rpc_uses_fixed_loopback_path_and_bridge_key_only(){
        use std::io::{BufRead,BufReader};
        let listener=std::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST,0)).unwrap();
        let port=listener.local_addr().unwrap().port();
        let server=std::thread::spawn(move||{
            let (mut stream,_)=listener.accept().unwrap();
            let mut reader=BufReader::new(stream.try_clone().unwrap());
            let mut headers=String::new();let mut line=String::new();let mut length=0;
            loop {line.clear();reader.read_line(&mut line).unwrap();headers.push_str(&line);if line=="\r\n" {break}if line.to_lowercase().starts_with("content-length:"){length=line.split(':').nth(1).unwrap().trim().parse::<usize>().unwrap()}}
            let mut body=vec![0;length];reader.read_exact(&mut body).unwrap();
            let result=br#"{"ok":true,"state":"connected"}"#;
            write!(stream,"HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",result.len()).unwrap();stream.write_all(result).unwrap();
            (headers,serde_json::from_slice::<Value>(&body).unwrap())
        });
        let value=post_rpc(port,"chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/",&json!({"operation":"get_status"}),"test-bridge-key-with-at-least-32-characters").unwrap();
        assert_eq!(value["state"],"connected");
        let (headers,body)=server.join().unwrap();
        assert!(headers.starts_with("POST /browser-bridge/rpc HTTP/1.1\r\n"));
        assert!(headers.contains("X-Creative-Bridge-Key: test-bridge-key-with-at-least-32-characters"));
        assert!(!headers.contains("Authorization:"));
        assert_eq!(body["origin"],"chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/");
        assert_eq!(body["request"]["operation"],"get_status");
        assert!(body.get("token").is_none());
    }
    #[test] fn http_head_rejects_redirects_chunking_and_large_bodies(){
        assert!(http_response_head(b"HTTP/1.1 302 Found\r\nContent-Length: 0").is_err());
        assert!(http_response_head(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked").is_err());
        assert!(http_response_head(b"HTTP/1.1 200 OK\r\nContent-Length: 300000").is_err());
    }
}
