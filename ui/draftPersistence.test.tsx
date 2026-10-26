import {expect,test} from 'vitest';
import {DraftPersistence} from './draftPersistence';

test('serialized saves use the previous acknowledged version and unchanged drafts are skipped',async()=>{
 const calls:any[]=[];let version=0;
 const repository=new DraftPersistence(async(_path,body)=>{calls.push(body);return {identity:'inspiration',version:++version,content:body.content}});
 await repository.hydrate('P',[]);
 await Promise.all([repository.save('P','inspiration',{question:'one'}),repository.save('P','inspiration',{question:'two'})]);
 await repository.save('P','inspiration',{question:'two'});
 expect(calls.map(c=>c.expected_version)).toEqual([0,1]);
});

test('a conflicting window keeps editing its recovered draft instead of overwriting the other window',async()=>{
 const calls:any[]=[];let n=0;
 const repository=new DraftPersistence(async(path,body)=>{calls.push({path,...body});return {identity:'recovered-123',version:++n,recovered_from:'inspiration',content:body.content}});
 await repository.hydrate('P',[]);
 await repository.save('P','inspiration',{question:'mine'});
 await repository.save('P','inspiration',{question:'mine edited'});
 expect(calls[1].path).toBe('/projects/P/drafts/recovered-123');expect(calls[1].expected_version).toBe(1);
});

test('uncertain local save retries with its operation id and project boundaries remain separate',async()=>{
 const calls:any[]=[];let fail=true;
 const repository=new DraftPersistence(async(path,body)=>{calls.push({path,...body});if(fail){fail=false;throw Error('offline')};return {identity:'inspiration',version:1,content:body.content}});
 await repository.hydrate('P',[]);await repository.hydrate('Q',[]);
 await expect(repository.save('P','inspiration',{question:'mine'})).rejects.toThrow();
 await repository.save('P','inspiration',{question:'mine'});await repository.save('Q','inspiration',{question:'other'});
 expect(calls[0].operation_id).toBe(calls[1].operation_id);expect(calls[2].expected_version).toBe(0);
});

test('an unknown save response is not acknowledged as a saved draft and can be retried',async()=>{
 let valid=false;const bodies:any[]=[];
 const repository=new DraftPersistence(async(_path,body)=>{bodies.push(body);return valid?{identity:'inspiration',version:1,content:body.content}:[] as any});
 await repository.hydrate('P',[]);
 await expect(repository.save('P','inspiration',{question:'保留输入'})).rejects.toThrow('未确认');
 valid=true;await repository.save('P','inspiration',{question:'保留输入'});
 expect(bodies[0].operation_id).toBe(bodies[1].operation_id);
});

test('server defaults and key ordering do not create untouched-window saves or recovery copies',async()=>{
 const calls:any[]=[];const cache={active:'s',drafts:{s:{title:'草稿',rows:[{start_ms:0,end_ms:3000,dialogue:'台词'}],confirmed:false}}};
 const normalized={identity:'script-editor',version:4,content:{question:'',analysisMode:'inspiration',analysisKind:'',dateBasis:'published',selected:[],referenceIds:[],useHistory:false,pendingExample:null,scriptDrafts:cache}};
 const sender=async(path:string,body:any)=>{calls.push({path,body});return {...normalized,version:5}};
 const first=new DraftPersistence(sender),second=new DraftPersistence(sender);
 await first.hydrate('P',[normalized]);await second.hydrate('P',[normalized]);
 const same={scriptDrafts:{drafts:{s:{confirmed:false,rows:[{dialogue:'台词',end_ms:3000,start_ms:0}],title:'草稿'}},active:'s'}};
 await first.save('P','script-editor',same);await second.save('P','script-editor',same);
 expect(calls).toHaveLength(0);
});
