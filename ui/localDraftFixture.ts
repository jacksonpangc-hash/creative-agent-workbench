/** Isolate unrelated editor tests from the project-local autosave API.
 * Persistence, conflicts and restart behavior have their own integration tests.
 */
export function localDraftFixture(url:string,init?:RequestInit):Response|undefined {
 if(url.endsWith('/drafts'))return new Response(JSON.stringify({drafts:[],active_identity:null}));
 if(url.includes('/drafts/')&&init?.method==='PUT'){
  const body=JSON.parse(String(init.body));return new Response(JSON.stringify({identity:url.split('/').at(-1),version:body.expected_version+1,content:body.content}));
 }
}
