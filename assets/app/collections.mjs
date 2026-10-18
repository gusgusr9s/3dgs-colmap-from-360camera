// Search and ordering on complete local collections; gallery queries run on the server.
const collator=new Intl.Collator('th',{numeric:true,sensitivity:'base'});
export function compareItems(a,b,order='original') {
  if(order==='name-asc'||order==='name-desc')return collator.compare(a.name,b.name)*(order==='name-desc'?-1:1)||a.index-b.index;
  if(order==='recent'||order==='oldest')return (a.time-b.time)*(order==='recent'?-1:1)||a.index-b.index;
  return a.index-b.index;
}
export function numberValue(value){const number=Number(value);return Number.isFinite(number)?number:Date.parse(value)||0;}

function toolbar(name,key,options) {
  const form=document.createElement('form');form.className='collection-toolbar';form.setAttribute('role','search');
  const label=document.createElement('label');label.textContent='ค้นหา'+name;
  const input=document.createElement('input');input.type='search';input.placeholder='พิมพ์ชื่อหรือรายละเอียด';input.setAttribute('aria-label','ค้นหา'+name);label.append(input);
  const orderLabel=document.createElement('label');orderLabel.textContent='เรียงตาม';
  const select=document.createElement('select');select.setAttribute('aria-label','เรียงลำดับ'+name);
  for(const [value,text]of options){const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);}
  orderLabel.append(select);
  const clear=document.createElement('button');clear.type='button';clear.textContent='ล้างค้นหา';clear.hidden=true;clear.dataset.icon='close';
  const count=document.createElement('span');count.className='muted';count.setAttribute('role','status');
  form.append(label,orderLabel,clear,count);form.addEventListener('submit',event=>event.preventDefault());
  const params=new URLSearchParams(location.search);input.value=params.get(key+'-q')||'';
  if([...select.options].some(o=>o.value===params.get(key+'-sort')))select.value=params.get(key+'-sort');
  function remember(){const params=new URLSearchParams(location.search);
    for(const [name,value]of [[key+'-q',input.value.trim()],[key+'-sort',select.value==='original'?'':select.value]])
      if(value)params.set(name,value);else params.delete(name);
    const query=params.toString();history.replaceState(null,'',location.pathname+(query?'?'+query:'')+location.hash);
    clear.hidden=!input.value;
  }
  return {form,input,select,clear,count,remember};
}
export function setupCollections(root=document){
  for(const list of root.querySelectorAll('[data-collection]')){
    if(list.dataset.collectionReady)continue;list.dataset.collectionReady='true';
    const selector=list.dataset.collectionItem||'.resource';
    const rows=[...list.children].filter(row=>row.matches(selector));
    if(!rows.length)continue;
    let group=null;const groups=new Map();
    for(const child of list.children){if(child.matches('.view-group'))group=child;else if(rows.includes(child))groups.set(child,group);}
    const data=rows.map((row,index)=>({row,index,group:groups.get(row),name:(row.querySelector('.resource-title,h2,h3')?.textContent||row.textContent).trim(),
      text:row.textContent.toLocaleLowerCase(),time:numberValue(row.dataset.orderTime)||-index}));
    const controls=toolbar(list.dataset.collection,list.dataset.collectionKey||'items', [['original','ลำดับเดิม'],['name-asc','ชื่อ ก–ฮ / A–Z'],['name-desc','ชื่อ ฮ–ก / Z–A'],['recent','ใหม่ล่าสุด'],['oldest','เก่าสุด']]);
    const empty=document.createElement('p');empty.className='empty';empty.textContent='ไม่พบรายการ ลองเปลี่ยนคำค้น';empty.hidden=true;
    list.before(controls.form);list.after(empty);
    function update(){const query=controls.input.value.trim().toLocaleLowerCase();let visible=0;
      for(const item of data){item.row.hidden=!item.row.textContent.toLocaleLowerCase().includes(query);if(!item.row.hidden)visible++;}
      for(const heading of new Set(groups.values()))if(heading)heading.hidden=!data.some(item=>item.group===heading&&!item.row.hidden);
      for(const heading of new Set(groups.values())){
        const sorted=data.filter(item=>item.group===heading).sort((a,b)=>compareItems(a,b,controls.select.value));
        if(heading){let previous=heading;for(const item of sorted){previous.after(item.row);previous=item.row;}}
        else for(const item of sorted)list.append(item.row);
      }
      controls.count.textContent=`${visible} / ${data.length} รายการ`;empty.hidden=visible>0;controls.remember();
    }
    controls.input.addEventListener('input',update);controls.select.addEventListener('change',update);
    controls.clear.addEventListener('click',()=>{controls.input.value='';update();controls.input.focus();});update();
  }
}

export function setupTables(root=document){
  for(const [index,table]of [...root.querySelectorAll('.benchmark-scroll table')].entries()){
    const rows=[...table.rows].filter(row=>!row.querySelector('th'));if(rows.length<4)continue;
    const headings=[...table.querySelectorAll('th')].map(th=>th.textContent.trim());
    const controls=toolbar('ตาราง','table-'+index,[['original','ลำดับเดิม'],...headings.flatMap((name,i)=>[[i+'-asc',name+' ↑'],[i+'-desc',name+' ↓']])]);
    table.parentElement.before(controls.form);
    const originals=new Map(rows.map((row,i)=>[row,i]));
    function value(row,column){const text=row.cells[column]?.textContent.trim()||'';
      const duration=text.match(/^(\d+):(\d+)$/);if(duration)return Number(duration[1])*60+Number(duration[2]);
      const number=text.match(/^([\d,.]+)\s*(?:GB|dB|$)/);return number?Number(number[1].replaceAll(',','')):text;}
    function update(){let visible=0;for(const row of rows){row.hidden=!row.textContent.toLocaleLowerCase().includes(controls.input.value.trim().toLocaleLowerCase());if(!row.hidden)visible++;}
      const [column,direction]=controls.select.value.split('-');const sorted=[...rows].sort((a,b)=>{
        if(column==='original')return originals.get(a)-originals.get(b);
        const av=value(a,Number(column)),bv=value(b,Number(column));return (typeof av==='number'&&typeof bv==='number'?av-bv:collator.compare(String(av),String(bv)))*(direction==='desc'?-1:1)||originals.get(a)-originals.get(b);});
      for(const row of sorted)row.parentElement.append(row);
      controls.count.textContent=`${visible} / ${rows.length} แถว`;controls.remember();}
    controls.input.addEventListener('input',update);controls.select.addEventListener('change',update);controls.clear.addEventListener('click',()=>{controls.input.value='';update();});update();
  }
}

export function setupChapterSearch(root=document){
  const chapters=root.querySelector('.tutorial-chapters');if(!chapters)return;
  const label=document.createElement('label');label.className='chapter-search';label.textContent='ค้นหาในคู่มือ';
  const input=document.createElement('input');input.type='search';input.placeholder='เช่น เดินถ่าย, Mask, Model';label.append(input);chapters.prepend(label);
  const links=[...chapters.querySelectorAll('a[href^="#"]')];const count=document.createElement('span');count.className='muted';count.setAttribute('role','status');chapters.append(count);
  function update(){let visible=0;for(const link of links){const section=root.getElementById(link.hash.slice(1));if(!section)continue;
      const match=section.textContent.toLocaleLowerCase().includes(input.value.trim().toLocaleLowerCase());section.hidden=!match;link.hidden=!match;if(match)visible++;}
    count.textContent=input.value?`พบ ${visible} หัวข้อ`:'';}
  input.addEventListener('input',update);
}
