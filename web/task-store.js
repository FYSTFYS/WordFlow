'use strict';
// Task records live in the selected project's xlsxdir, not in browser storage.
const TaskStore = (() => {
 const FILE='task_records.xlsx';
 const TASK_FIELDS=['id','name','stage','status','workbook','sheet','sources','job_id','job_kind','result_dir','failed_count','import_done','card_config','error','created_at','updated_at','name_mode'];
 const LEGACY_TASK_FIELDS=TASK_FIELDS.slice(0,-1);
 const WORD_FIELDS=['task_id','kind','position','word'];
 const dec=new TextDecoder(),enc=new TextEncoder();
 const safe=s=>{s=String(s??'');if(/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(s)||s.length>32767)throw Error('任务记录包含不支持的字符或超过 Excel 单元格长度');return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/\r/g,'&#13;');};
 function col(n){let s='';for(n++;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
 function sheet(fields,rows){if(rows.length>1048575)throw Error('任务台账超过 Excel 工作表行数上限');return '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+[fields,...rows].map((row,i)=>`<row r="${i+1}">`+row.map((v,j)=>`<c r="${col(j)}${i+1}" t="inlineStr"><is><t xml:space="preserve">${safe(v)}</t></is></c>`).join('')+'</row>').join('')+'</sheetData></worksheet>';}
 function build(tasks){const words=[];for(const t of tasks)for(const kind of ['selected','imported'])for(const [index,word] of (t[kind]||[]).entries())words.push([t.id,kind,String(index),word]);
  const files={
   '[Content_Types].xml':'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
   '_rels/.rels':'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
   'xl/workbook.xml':'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="tasks" sheetId="1" r:id="rId1"/><sheet name="words" sheetId="2" r:id="rId2"/></sheets></workbook>',
   'xl/_rels/workbook.xml.rels':'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>',
   'xl/worksheets/sheet1.xml':sheet(TASK_FIELDS,tasks.map(t=>TASK_FIELDS.map(k=>t[k]||''))),
   'xl/worksheets/sheet2.xml':sheet(WORD_FIELDS,words),
  };return fflate.zipSync(Object.fromEntries(Object.entries(files).map(([k,v])=>[k,enc.encode(v)])));}
 function dom(bytes){const doc=new DOMParser().parseFromString(dec.decode(bytes),'application/xml');if(doc.querySelector('parsererror'))throw Error('任务台账 XML 无法解析');return doc;}
 function elements(root,name){return [...root.getElementsByTagNameNS('*',name)];}
 function colIndex(ref){let n=0;for(const c of (ref.match(/^[A-Z]+/i)?.[0]||'').toUpperCase())n=n*26+c.charCodeAt(0)-64;return n-1;}
 function rows(doc,shared){return elements(doc,'row').map(row=>{const result=[];for(const cell of elements(row,'c')){const i=colIndex(cell.getAttribute('r')||'');if(i<0)throw Error('任务台账单元格缺少坐标');const type=cell.getAttribute('t'),v=elements(cell,'v')[0]?.textContent||'';result[i]=type==='inlineStr'?elements(cell,'t').map(t=>t.textContent).join(''):type==='s'?(shared[Number(v)]??''):v;}return result;});}
 function loadBytes(bytes){const z=fflate.unzipSync(bytes);const need=n=>{if(!z[n])throw Error('任务台账缺少 '+n);return dom(z[n]);};const workbook=need('xl/workbook.xml'),rels=need('xl/_rels/workbook.xml.rels'),sheets=elements(workbook,'sheet');if(sheets.length!==2||sheets.map(s=>s.getAttribute('name')).join(',')!=='tasks,words')throw Error('任务台账工作表结构不匹配，未覆盖文件');
  const shared=z['xl/sharedStrings.xml']?elements(need('xl/sharedStrings.xml'),'si').map(s=>elements(s,'t').map(t=>t.textContent).join('')):[];
  const get=s=>{const id=s.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships','id');const rel=elements(rels,'Relationship').find(r=>r.getAttribute('Id')===id);if(!rel)throw Error('任务台账工作表关系缺失');const target=rel.getAttribute('Target');const parts=[];for(const part of (target.startsWith('/')?target.slice(1):'xl/'+target).split('/')){if(part==='..')parts.pop();else if(part&&part!=='.')parts.push(part);}const doc=need(parts.join('/'));if(elements(doc,'f').length)throw Error('任务台账包含公式，未覆盖文件');return rows(doc,shared);};
  const [taskRows,wordRows]=sheets.map(get);function matches(header,expected){return !!header&&header.length===expected.length&&expected.every((v,i)=>header[i]===v);}const taskFields=matches(taskRows[0],TASK_FIELDS)?TASK_FIELDS:matches(taskRows[0],LEGACY_TASK_FIELDS)?LEGACY_TASK_FIELDS:null;if(!taskFields||!matches(wordRows[0],WORD_FIELDS))throw Error('任务台账表头不匹配，未覆盖文件');if(taskRows.some(r=>r.length>taskFields.length)||wordRows.some(r=>r.length>WORD_FIELDS.length))throw Error('任务台账包含额外列，未覆盖文件');
  const tasks=taskRows.slice(1).map(row=>{const t=Object.fromEntries(taskFields.map((k,i)=>[k,row[i]||'']));if(!t.name_mode)t.name_mode=t.name?'manual':'auto';return t;});const index=new Map();for(const t of tasks){if(!t.id||index.has(t.id))throw Error('任务台账的任务编号无效或重复');t.selected=[];t.imported=[];index.set(t.id,t);}
  for(const row of wordRows.slice(1)){const [id,kind,position,word]=row;if(!index.has(id)||!['selected','imported'].includes(kind)||!/^\d+$/.test(position||''))throw Error('任务单词记录无效');index.get(id)[kind].push([Number(position),word||'']);}
  for(const t of tasks)for(const kind of ['selected','imported'])t[kind]=t[kind].sort((a,b)=>a[0]-b[0]).map(v=>v[1]);return tasks;}
 class Store{
  constructor(directory){this.directory=directory;this.handle=null;this.base=null;this.queue=Promise.resolve();}
  async load(){try{this.handle=await this.directory.getFileHandle(FILE);this.base=new Uint8Array(await (await this.handle.getFile()).arrayBuffer());return loadBytes(this.base);}catch(e){if(e.name==='NotFoundError'){this.handle=null;this.base=null;return [];}throw e;}}
  save(tasks){const snapshot=structuredClone(tasks);const work=async()=>{let handle=this.handle;if(!handle){try{await this.directory.getFileHandle(FILE);throw Error('任务台账已被创建，请重新打开项目目录');}catch(e){if(e.name!=='NotFoundError')throw e;}handle=await this.directory.getFileHandle(FILE,{create:true});this.handle=handle;this.base=new Uint8Array();}
   const current=new Uint8Array(await (await handle.getFile()).arrayBuffer());if(current.length!==this.base.length||current.some((v,i)=>v!==this.base[i]))throw Error('任务台账已被外部修改，请重新打开项目目录');
   const bytes=build(snapshot),stream=await handle.createWritable();try{await stream.write(bytes);await stream.close();}catch(e){await stream.abort();throw e;}this.base=bytes;};
   const result=this.queue.then(work);this.queue=result.catch(()=>{});return result;}
 }
 return {FILE,Store,build,loadBytes,TASK_FIELDS,WORD_FIELDS};
})();
if(typeof module!=='undefined')module.exports=TaskStore;
