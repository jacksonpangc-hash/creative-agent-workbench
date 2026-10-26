type Draft = {identity:string,version:number,content:Record<string,any>,recovered_from?:string};
type Sender = (path:string,body:Record<string,any>)=>Promise<Draft>;

// The API fills omitted form fields. Compare meaning, not object insertion
// order or whether a default was spelled out by this particular window.
const defaults={analysisMode:'inspiration',analysisKind:'',discussionOnly:false,question:'',
 selected:[],selectedSegments:[],ownAssetIds:[],useHistory:false,referenceIds:[],activeGroups:[],
 source:'',scopeSources:[],dateBasis:'published',startDate:'',endDate:'',minSpend:'',minRoas:'',
 account:'',query:'',discovery:false,pendingExample:null,scriptDrafts:null,parseContext:null,revisionContext:null};
function ordered(value:any):any{
 if(Array.isArray(value))return value.map(ordered);
 if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,ordered(value[key])]));
 return value;
}
const fingerprintOf=(content:Record<string,any>)=>JSON.stringify(ordered({...defaults,...content}));

/** Save locally in order; a conflict branches instead of overwriting another window. */
export class DraftPersistence {
 private versions=new Map<string,number>();
 private fingerprints=new Map<string,string>();
 private operations=new Map<string,string>();
 private aliases=new Map<string,string>();
 private pending:Promise<unknown>=Promise.resolve();
 constructor(private send:Sender){}
 async hydrate(project:string,drafts:Draft[]){
  for(const draft of drafts){const key=project+':'+draft.identity;this.versions.set(key,draft.version);this.fingerprints.set(key,fingerprintOf(draft.content))}
 }
 save(project:string,identity:string,content:Record<string,any>):Promise<Draft|undefined>{
  const snapshot=JSON.parse(JSON.stringify(content));
  const run=async()=>{
   const actual=this.aliases.get(project+':'+identity)||identity,key=project+':'+actual;
   const fingerprint=fingerprintOf(snapshot);
   if(this.fingerprints.get(key)===fingerprint)return;
   const operationKey=key+':'+fingerprint;
   if(!this.operations.has(operationKey))this.operations.set(operationKey,crypto.randomUUID());
   const saved=await this.send(`/projects/${project}/drafts/${actual}`,{expected_version:this.versions.get(key)||0,operation_id:this.operations.get(operationKey),content:snapshot});
   if(!saved||typeof saved.identity!=='string'||!Number.isInteger(saved.version)||saved.version<1||!saved.content||typeof saved.content!=='object')throw new Error('草稿保存未确认，输入已保留');
   this.versions.set(project+':'+saved.identity,saved.version);this.fingerprints.set(project+':'+saved.identity,fingerprint);
   if(saved.identity!==actual)this.aliases.set(project+':'+identity,saved.identity);
   this.operations.delete(operationKey);return saved;
  };
  const result=this.pending.then(run,run);this.pending=result.catch(()=>{});return result;
 }
}
