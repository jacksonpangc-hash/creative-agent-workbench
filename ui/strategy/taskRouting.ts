type Item=Record<string,any>;
export function isMaterialInspection(question:string){
 return /(我的|当前项目|已上传|上传的|导入的).{0,8}素材/.test(question)&&/检查|查询|查看|导入成功|处理状态|识别状态|上传成功/.test(question)&&!/写.{0,6}脚本|比较|创作方向|分析.{0,6}开头|卖点表达/.test(question);
}
export function primaryActionLabel(question:string){return /(?:写|生成|创作|给我).{0,12}(?:脚本|分镜稿)/.test(question)?'生成拍摄脚本':'发送'}
export function reliableTranscript(asset:Item){
 const supplied=asset.transcript_policy;
 if(supplied?.allowed&&supplied.execution_state==='succeeded'&&supplied.quality_state==='usable')return true;
 const m=asset.metadata||{};
 return typeof m.transcript==='string'&&!!m.transcript.trim()&&(m.transcription?.reviewed===true||['user_import','subtitle','user_subtitle'].includes(m.transcript_source)||(!m.transcription&&Array.isArray(m.subtitles)&&m.subtitles.length>0));
}
export function automaticAnalysisKind(assets:Item[],models:Item[],chosen?:Item){
 if(!assets.length||!assets.every(reliableTranscript))return 'video';
 const candidate=chosen||models.find(m=>m.has_key&&m.availability?.video_analysis?.available)||models.find(m=>m.has_key&&m.availability?.strategy?.available);
 return candidate?.availability?.strategy?.available&&!candidate?.availability?.video_analysis?.available?'spoken':'video';
}
