import {useEffect,useRef,useState} from 'react';
import {Alert,App,Button,Card,Input,Modal,Popconfirm,Select,Space,Switch,Table,Tag,Typography} from 'antd';
import {HttpUtil} from '@/utils';
type Kind='messages'|'buttons'|'phrases';
type Row={kind:Kind;original:string;current:string;changed:boolean};
type Group={total:number;changed:number};
type Result={rows:Row[];total:number;page:number;stats:Record<Kind,Group>};
const names:Record<string,string>={all:'همه متن‌ها',messages:'پیام‌های آماده',buttons:'عنوان دکمه‌ها',phrases:'عبارت‌های پیام‌ها'};
const empty:Result={rows:[],total:0,page:1,stats:{messages:{total:0,changed:0},buttons:{total:0,changed:0},phrases:{total:0,changed:0}}};
const paragraph:React.CSSProperties={whiteSpace:'pre-wrap',overflowWrap:'anywhere',lineHeight:1.9};
export default function BotTextsAdminPage(){
 const {message,modal}=App.useApp();
 const [data,setData]=useState<Result>(empty);
 const [kind,setKind]=useState('all');
 const [query,setQuery]=useState('');
 const [changedOnly,setChangedOnly]=useState(false);
 const [page,setPage]=useState(1);
 const [loading,setLoading]=useState(false);
 const [busy,setBusy]=useState(false);
 const [selected,setSelected]=useState<Row|null>(null);
 const [draft,setDraft]=useState('');
 const sequence=useRef(0);
 const call=async(action:string,values:Record<string,unknown>={})=>{
  const r=await HttpUtil.post('/panel/api/sales-bot',{action,...values},
    {headers:{'Content-Type':'application/json'},silent:true,timeout:65000});
  if(!r?.success)throw new Error(r?.msg||'عملیات ناموفق');
  return r.obj as any;
 };
 const load=async()=>{
  const id=++sequence.current;
  setLoading(true);
  try{
   const result=await call('text_catalog_get',{kind,query,page,page_size:20,changed_only:changedOnly}) as Result;
   if(id===sequence.current)setData(result);
  }catch(e){if(id===sequence.current)message.error(String(e))}
  finally{if(id===sequence.current)setLoading(false)}
 };
 useEffect(()=>{
  const timer=setTimeout(()=>{void load()},query?250:0);
  return ()=>clearTimeout(timer);
 },[kind,query,page,changedOnly]);
 const save=async(row:Row,operation:'save'|'reset',value?:string)=>{
  if(busy)return;
  setBusy(true);
  try{
   await call('text_catalog_update',{kind:row.kind,key:row.original,
      expected:row.current,operation,...(operation==='save'?{value}:{})});
   message.success(operation==='save'?'متن با موفقیت ذخیره شد':'متن اصلی بازگردانده شد');
   setSelected(null);
   await load();
  }catch(e){message.error(String(e));await load()}
  finally{setBusy(false)}
 };
 const synchronize=()=>modal.confirm({
  title:'به‌روزرسانی متن‌های قابل‌ویرایش',
  content:'موارد جدید از سورس شناسایی می‌شوند و متن‌هایی که قبلاً تغییر داده‌اید حفظ خواهند شد.',
  okText:'به‌روزرسانی',cancelText:'انصراف',
  onOk:async()=>{setBusy(true);try{await call('text_catalog_sync');await load();message.success('فهرست تازه شد')}
      catch(e){message.error(String(e));throw e}finally{setBusy(false)}}
 });
 const stats=data.stats;
 const count=Object.values(stats).reduce((n,x)=>n+x.total,0);
 const modified=Object.values(stats).reduce((n,x)=>n+x.changed,0);
 const columns:any[]=[
  {title:'نوع',dataIndex:'kind',width:130,render:(x:Kind)=><Tag>{names[x]}</Tag>},
  {title:'متن اصلی',dataIndex:'original',render:(x:string)=><Typography.Text style={paragraph}>{x}</Typography.Text>},
  {title:'متن فعلی',dataIndex:'current',render:(x:string,row:Row)=><Space direction="vertical" size={3}>
      <Typography.Text style={paragraph}>{x}</Typography.Text>{row.changed&&<Tag color="blue">ویرایش‌شده</Tag>}
   </Space>},
  {title:'عملیات',width:190,render:(_:unknown,row:Row)=><Space wrap>
    <Button type="primary" size="small" onClick={()=>{setSelected(row);setDraft(row.current)}}>ویرایش</Button>
    <Popconfirm title="این مورد به متن پیش‌فرض برگردد؟" onConfirm={()=>void save(row,'reset')}
      okText="بازگردانی" cancelText="انصراف">
      <Button size="small" disabled={!row.changed||busy}>بازنشانی</Button>
    </Popconfirm>
   </Space>}
 ];
 return <>
  <Card title="مدیریت کامل متن‌ها و عنوان دکمه‌های ربات"
    extra={<Button disabled={busy} onClick={synchronize}>شناسایی متن‌های جدید</Button>}>
   <Alert type="info" showIcon style={{marginBottom:14}}
    title="ویرایش متن‌ها بدون تغییر سورس و بدون ری‌استارت ربات"
    description="متن پیام‌ها، دکمه‌ها و عبارت‌های موجود در پاسخ‌های پویا قابل تغییرند. عملکرد دکمه‌ها و شناسه آن‌ها ثابت می‌ماند."/>
   <Space wrap style={{marginBottom:16}}>
    <Tag color="blue">کل: {count.toLocaleString('fa-IR')}</Tag>
    <Tag color="green">ویرایش‌شده: {modified.toLocaleString('fa-IR')}</Tag>
    {(['messages','buttons','phrases'] as Kind[]).map(k=>
      <Tag key={k}>{names[k]}: {stats[k].total.toLocaleString('fa-IR')}</Tag>)}
   </Space>
   <Space wrap style={{display:'flex',marginBottom:16}}>
    <Input.Search placeholder="جستجو در متن اصلی یا متن فعلی" allowClear
     value={query} onChange={e=>{setQuery(e.target.value);setPage(1)}}
     style={{width:300,maxWidth:'100%'}}/>
    <Select value={kind} style={{width:220}}
     onChange={v=>{setKind(v);setPage(1)}}
     options={Object.entries(names).map(([value,label])=>({value,label}))}/>
    <Space><Switch checked={changedOnly} onChange={v=>{setChangedOnly(v);setPage(1)}}/>
     فقط متن‌های ویرایش‌شده</Space>
    <Button onClick={()=>void load()} loading={loading}>تازه‌سازی</Button>
   </Space>
   <Table<Row> rowKey={r=>r.kind+':'+r.original} dataSource={data.rows}
    columns={columns} loading={loading} scroll={{x:800}} size="middle"
    pagination={{current:data.page,pageSize:20,total:data.total,showSizeChanger:false,
     showTotal:t=>t.toLocaleString('fa-IR')+' مورد',onChange:p=>setPage(p)}}/>
   <Typography.Text type="secondary">عبارت‌های پویا ممکن است داخل متن‌های طولانی‌تر استفاده شوند؛ برای تغییر ایمن، فقط عبارت موردنظر را ویرایش کنید.</Typography.Text>
  </Card>
  <Modal open={!!selected} width={720} title={'ویرایش '+(selected?names[selected.kind]:'')}
   okText="ذخیره" cancelText="انصراف" confirmLoading={busy}
   onCancel={()=>setSelected(null)}
   okButtonProps={{disabled:!draft.trim()||draft===selected?.current||
      (selected?.kind==='buttons'&&(draft.includes('\n')||draft.length>64))}}
   onOk={()=>{if(selected)void save(selected,'save',draft)}}>
   <Space direction="vertical" size="middle" style={{width:'100%'}}>
    <Typography.Text type="secondary">متن پیش‌فرض</Typography.Text>
    <div style={{background:'#f1f5f9',padding:12,borderRadius:8,...paragraph}}>{selected?.original}</div>
    <Typography.Text strong>متن جایگزین</Typography.Text>
    <Input.TextArea rows={Math.min(8,Math.max(3,draft.split('\n').length+1))}
       value={draft} maxLength={selected?.kind==='buttons'?64:4000} showCount
       onChange={e=>setDraft(e.target.value)} style={{lineHeight:1.9}}/>
    <Alert type="warning" showIcon
      title="متغیرهای درون متن و آدرس‌های اصلی را حفظ کنید"
      description="متغیرهایی مانند {name} و لینک‌های موجود در متن اولیه باید بدون تغییر باقی بمانند."/>
   </Space>
  </Modal>
 </>;
}