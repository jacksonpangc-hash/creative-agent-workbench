/** Poll metadata only; keep one refresh in flight and commit revisions after success. */
export class ActivityRefresh {
 private revision='';private running=false;private active=true;
 constructor(private read:()=>Promise<{revision:string}>,private refresh:()=>Promise<unknown>){}
 stop(){this.active=false}
 async poll(){if(this.running||!this.active)return;this.running=true;try{const value=await this.read();if(this.active&&typeof value.revision==='string'&&value.revision!==this.revision){await this.refresh();if(this.active)this.revision=value.revision}}finally{this.running=false}}
}
