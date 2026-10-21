import {useEffect,useState} from 'react';
import {Alert,App,Button,Card,Checkbox,DatePicker,Form,Input,InputNumber,Modal,Space,Table,Tag,Typography} from 'antd';
import dayjs from 'dayjs';
type Client={id:number;email:string;subId:string;totalGB:number;expiryTime:number;enable:boolean;traffic?:{up:number;down:number}};
type Inbound={id:number;remark:string;protocol:string;enable:boolean};
type Profile={protocol:string;filename:string;content:string};
const prefix=()=>((window as any).__masihaNativePanel||window.X_UI_BASE_PATH||'/xui/').replace(/\/$/,'')+'/panel/api/';
async function api(path:string,method='GET',body?:unknown){
 const res=await fetch(prefix()+path,{method,credentials:'same-origin',cache:'no-store',
 headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest',
 ...(method==='POST'?{'Content-Type':'application/json','X-CSRF-Token':document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content||''}:{})},
 ...(method==='POST'?{body:JSON.stringify(body)}:{})});
 const json=await res.json();
 if(!res.ok||json.success!==true)throw new Error(json.msg||'درخواست ناموفق بود');
 return json.obj;
}
export default function UnifiedAccountsPage(){
 const {message}=App.useApp();
 const [data,setData]=useState<Client[]>([]);
 const [inbounds,setInbounds]=useState<Inbound[]>([]);
 const [form]=Form.useForm();
 const [creating,setCreating]=useState(false);
 const [busy,setBusy]=useState(false);
 const [profiles,setProfiles]=useState<{name:string;items:Profile[]}|null>(null);
 const load=async()=>{
  try {
   const [c,i]=await Promise.all([api('clients/list'),api('inbounds/list')]);
   setData(Array.isArray(c)?c:[]);
   setInbounds(Array.isArray(i)?i:[]);
  }catch(e){message.error(String(e))}
 };
 useEffect(()=>{void load()},[]);
 const create=async()=>{
  try{
   const v=await form.validateFields();
   const bytes=Math.round(v.volumeGB*1024**3);
   if(!Number.isSafeInteger(bytes))throw new Error('حجم نامعتبر');
   const expires=v.expiry?v.expiry.valueOf():0;
   if(expires&&expires<=Date.now())throw new Error('انقضا باید در آینده باشد');
   setBusy(true);
   await api('clients/add','POST',{client:{email:v.email,totalGB:bytes,expiryTime:expires,enable:true,comment:v.comment||''},inboundIds:v.inboundIds});
   message.success('کاربر در x-ui ساخته شد؛ پروفایل‌های جانبی توسط همگام‌ساز ایجاد می‌شوند.');
   form.resetFields();setCreating(false);await load();
  }catch(e){message.error(String(e))}finally{setBusy(false)}
 };
 const getProfiles=async(c:Client)=>{
  if(!c.subId){message.warning('این کاربر شناسه اشتراک ندارد');return}
  setBusy(true);
  try{
   const all=await api('masiha-profiles','POST',{subId:c.subId});
   setProfiles({name:c.email,items:Array.isArray(all)?all:[]});
  }catch(e){message.error(String(e))}finally{setBusy(false)}
 };
 const download=(file:Profile)=>{
  const u=URL.createObjectURL(new Blob([file.content],{type:'text/plain;charset=utf-8'}));
  const a=document.createElement('a');a.href=u;a.download=file.filename;a.click();
  setTimeout(()=>URL.revokeObjectURL(u),1000);
 };
 return <div dir="rtl" style={{padding:24}}>
  <Space direction="vertical" size="large" style={{width:'100%'}}>
   <Typography.Title level={3}>اشتراک مشترک Masiha و x-ui</Typography.Title>
   <Alert showIcon type="info" title="یک حجم و یک زمان انقضا برای تمام پروتکل‌ها"
    description="حجم و انقضا در رکورد اصلی x-ui قرار می‌گیرند. OpenVPN و OpenConnect خودکار ساخته می‌شوند. L2TP/IPsec و IKEv2/IPsec پس از راه‌اندازی ایمن سرویس و حسابداری مصرف به این رکورد وصل خواهند شد."/>
   <Card><Space wrap>
    <Button type="primary" onClick={()=>{form.resetFields();form.setFieldsValue({volumeGB:20,inboundIds:inbounds.filter(i=>i.enable).map(i=>i.id)});setCreating(true)}}>ساخت کاربر x-ui</Button>
    <Button onClick={()=>void load()}>بروزرسانی</Button><Tag>کل کاربران: {data.length}</Tag>
   </Space></Card>
   <Table<Client> rowKey="email" dataSource={data} scroll={{x:820}} pagination={{pageSize:15}} columns={[
    {title:'شناسه',dataIndex:'email'},
    {title:'سهمیه',render:(_,r)=>r.totalGB?(r.totalGB/1024**3).toFixed(2)+' GB':'نامحدود'},
    {title:'مصرف',render:(_,r)=>(((r.traffic?.up||0)+(r.traffic?.down||0))/1024**3).toFixed(2)+' GB'},
    {title:'انقضا',render:(_,r)=>r.expiryTime?dayjs(r.expiryTime).format('YYYY-MM-DD HH:mm'):'نامحدود'},
    {title:'وضعیت',render:(_,r)=><Tag color={r.enable?'green':'red'}>{r.enable?'فعال':'غیرفعال'}</Tag>},
    {title:'حساب‌ها',render:(_,r)=><Button disabled={busy} onClick={()=>void getProfiles(r)}>پروفایل‌های VPN</Button>}
   ]}/>
  </Space>
  <Modal open={creating} title="کاربر جدید با سهمیه مشترک" okText="ساخت در x-ui" cancelText="انصراف" confirmLoading={busy} onOk={()=>void create()} onCancel={()=>setCreating(false)}>
   <Form form={form} layout="vertical">
    <Form.Item name="email" label="شناسه کاربر" rules={[{required:true},{pattern:/^[A-Za-z0-9][A-Za-z0-9_.@-]{2,99}$/}]}><Input dir="ltr" autoComplete="off"/></Form.Item>
    <Form.Item name="volumeGB" label="حجم کل به گیگابایت (۰=نامحدود)" rules={[{required:true}]}><InputNumber min={0} max={100000} style={{width:'100%'}}/></Form.Item>
    <Form.Item name="expiry" label="زمان انقضا"><DatePicker showTime style={{width:'100%'}}/></Form.Item>
    <Form.Item name="inboundIds" label="اینباندهای x-ui" rules={[{required:true,type:'array',min:1}]}><Checkbox.Group options={inbounds.filter(i=>i.enable).map(i=>({label:i.remark+' ('+i.protocol+')',value:i.id}))}/></Form.Item>
    <Form.Item name="comment" label="توضیحات"><Input maxLength={200}/></Form.Item>
   </Form>
  </Modal>
  <Modal open={!!profiles} title={'حساب‌های '+(profiles?.name||'')} width={650} onCancel={()=>setProfiles(null)} footer={<Button onClick={()=>setProfiles(null)}>بستن</Button>}>
   {profiles?.items.length?<Space direction="vertical" style={{width:'100%'}}>
    {profiles.items.map((p,i)=><Card size="small" key={p.protocol+'-'+i} title={p.protocol.toUpperCase()} extra={<Button onClick={()=>download(p)}>دریافت</Button>}>
      <Typography.Paragraph copyable={{text:p.content}} style={{whiteSpace:'pre-wrap',direction:'ltr'}}>{p.content}</Typography.Paragraph>
    </Card>)}
   </Space>:<Alert showIcon type="warning" title="پروفایل‌ها هنوز آماده نیستند"/>}
  </Modal>
 </div>;
}