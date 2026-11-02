'use strict';
// Adapts the local server's project data endpoints to the handle operations used by the UI.
const DefaultProject=(()=>{
 const encoder=new TextEncoder();
 async function request(path,{method='GET',body}={}){
  const response=await fetch(path,{method,headers:method==='POST'?{'Content-Type':'application/json'}:undefined,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
  const data=await response.json();if(!response.ok){const message=data.error||`HTTP ${response.status}`;if(response.status===404)throw new DOMException(message,'NotFoundError');if(response.status===403)throw new DOMException(message,'NotAllowedError');if(response.status===409)throw new DOMException(message,'InvalidStateError');throw Error(message);}return data;
 }
 function address(path){return encodeURIComponent(path);}
 function toBase64(bytes){let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(binary);}
 function fromBase64(data){const text=atob(data),bytes=new Uint8Array(text.length);for(let i=0;i<text.length;i++)bytes[i]=text.charCodeAt(i);return bytes;}
 function join(parent,name){if(!name||name.includes('/')||name.includes('\\')||name==='.'||name==='..')throw new TypeError('文件名无效');return parent?`${parent}/${name}`:name;}
 class RemoteFile{
  constructor(path,name){this.path=path;this.name=name;this.kind='file';this.version=null;}
  async getFile(){const result=await request('/api/project/file?path='+address(this.path));this.version=result.sha256;return new File([fromBase64(result.data)],this.name);}
  async createWritable(){if(this.version===null)await this.getFile();const handle=this,expected=this.version;let buffer=null,closed=false;return {
   async write(value){if(closed)throw Error('文件写入流已关闭');if(typeof value==='string')buffer=encoder.encode(value);else if(value instanceof Uint8Array)buffer=value;else if(value instanceof ArrayBuffer)buffer=new Uint8Array(value);else throw Error('不支持的文件写入类型');},
   async close(){if(closed)throw Error('文件写入流已关闭');if(buffer===null)throw Error('没有可保存的内容');const result=await request('/api/project/write',{method:'POST',body:{path:handle.path,data:toBase64(buffer),sha256:expected}});handle.version=result.sha256;closed=true;},
   async abort(){closed=true;buffer=null;}
  };}
 }
 class RemoteDir{
  constructor(path,name){this.path=path;this.name=name;this.kind='directory';}
  async getDirectoryHandle(name){const path=join(this.path,name),result=await request('/api/project/entry?path='+address(path));if(result.kind!=='directory')throw new DOMException('目标不是目录','TypeMismatchError');return new RemoteDir(path,name);}
  async getFileHandle(name,options={}){const path=join(this.path,name),result=options.create?await request('/api/project/create',{method:'POST',body:{path}}):await request('/api/project/entry?path='+address(path));if(result.kind!=='file')throw new DOMException('目标不是文件','TypeMismatchError');return new RemoteFile(path,name);}
  async *entries(){const result=await request('/api/project/list?path='+address(this.path));for(const item of result.entries){const path=join(this.path,item.name);yield [item.name,item.kind==='directory'?new RemoteDir(path,item.name):new RemoteFile(path,item.name)];}}
 }
 async function open(){const info=await request('/api/project/init',{method:'POST',body:{}});return {root:new RemoteDir('',info.name),path:info.path};}
 return {open};
})();
if(typeof module!=='undefined')module.exports=DefaultProject;
