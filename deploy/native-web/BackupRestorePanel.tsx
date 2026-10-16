import {useEffect,useMemo,useState} from 'react';
import {Alert,Button,Card,Checkbox,Input,Space,Table,Tag,Typography,Upload,App} from 'antd';
import type {UploadFile} from 'antd';
import {HttpUtil} from '@/utils';
import {httpRequest} from '@/api/http-init';

type BackupEntry={name:string,size:number,created:string};
type JobEntry={job:string,state:string,detail?:string,at?:number};
type BackupStatus={backups:BackupEntry[],jobs:JobEntry[],has_key:boolean};
const endpoint='/panel/api/masiha-backup';

const humanSize=(n:number)=>n>1048576?(n/1048576).toFixed(1)+' MB':(n/1024).toFixed(0)+' KB';

export default function BackupRestorePanel(){
 const {message}=App.useApp();
 const [status,setStatus]=useState<BackupStatus>({backups:[],jobs:[],has_key:false});
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 const [archive,setArchive]=useState<UploadFile[]>([]);
 const [key,setKey]=useState<UploadFile[]>([]);
 const [importID,setImportID]=useState('');
 const [confirmation,setConfirmation]=useState('');
 const [acknowledged,setAcknowledged]=useState(false);
 const [restoreMode,setRestoreMode]=useState(false);
 const jobs=status.jobs;
 const latestJob=useMemo(()=>jobs.sort((a,b)=>(b.at||0)-(a.at||0)).slice(0,7),[jobs]);
 const currentImport=jobs.find(j=>j.job===importID);
 const root=(window.X_UI_BASE_PATH||'/').replace(/\/$/,'');
 const url=(name:string)=>root+endpoint+'/download/'+encodeURIComponent(name);
 const reload=async()=>{
  const result=await HttpUtil.get<BackupStatus>(endpoint+'/status',undefined,{silent:true,timeout:15000});
  if(result.success&&result.obj){setStatus(result.obj);setError('')}
  else setError(result.msg||'خواندن وضعیت بکاپ ناموفق بود');
 };
 useEffect(()=>{
  void reload();
  const timer=setInterval(()=>void reload(),7000);
  return ()=>clearInterval(timer);
 },[]);
 const create=async()=>{
  setBusy(true);
  try{
   const r=await HttpUtil.post(endpoint+'/create',{}, {headers:{'Content-Type':'application/json'},silent:true,timeout:15000});
   if(!r.success)throw new Error(r.msg||'ساخت بکاپ آغاز نشد');
   message.success('ساخت بکاپ آغاز شد. پس از تکمیل، دانلود فعال می‌شود.');
   await reload();
  }catch(e){message.error(String(e))}
  finally{setBusy(false)}
 };
 const upload=async()=>{
  if(!archive[0]?.originFileObj||!key[0]?.originFileObj){
   message.error('فایل بکاپ رمزگذاری‌شده و فایل کلید را انتخاب کنید');return;
  }
  const form=new FormData();
  form.append('archive',archive[0].originFileObj);
  form.append('key',key[0].originFileObj);
  setBusy(true);setImportID('');setRestoreMode(false);
  try{
   const r=await httpRequest('POST',endpoint+'/import',form,{timeout:300000});
   const data=r.data as {success:boolean,msg?:string,obj?:{job:string}};
   if(!data?.success||!data.obj?.job)throw new Error(data?.msg||'آپلود ناموفق');
   setImportID(data.obj.job);
   message.success('فایل دریافت شد؛ بررسی سلامت و رمزگشایی آغاز شد');
   await reload();
  }catch(e){message.error(String(e))}
  finally{setBusy(false)}
 };
 const restore=async()=>{
  if(!importID||currentImport?.state!=='done'||confirmation!=='RESTORE'||!acknowledged)return;
  setBusy(true);
  try{
   const r=await HttpUtil.post(endpoint+'/restore',
    {job:importID,confirm:confirmation},
    {headers:{'Content-Type':'application/json'},silent:true,timeout:15000});
   if(!r.success)throw new Error(r.msg||'بازیابی آغاز نشد');
   message.warning('بازیابی آغاز شد. پنل ممکن است موقتاً از دسترس خارج شود. ربات مقصد به‌صورت خودکار روشن نخواهد شد.');
   setRestoreMode(false);
  }catch(e){message.error(String(e))}
  finally{setBusy(false)}
 };
 const statusTag=(job:JobEntry)=>{
  const labels:Record<string,[string,string]>={running:['blue','در حال اجرا'],done:['green','موفق'],failed:['red','خطا']};
  const [color,title]=labels[job.state]||['default',job.state];
  return <Tag color={color}>{title}</Tag>;
 };
 return <Space direction="vertical" size="large" style={{width:'100%'}}>
  <Alert type="info" showIcon title="انتقال کامل سرویس‌ها؛ بدون کتابخانه موزیک و فیلم"
   description="کلاینت‌ها، نودها، اینباندها، حجم و مصرف، اشتراک‌ها، تنظیمات، کیف پول و سفارش‌ها حفظ می‌شوند. فایل‌ها، مشخصات موزیک و فیلم، نظرات و پلی‌لیست‌ها منتقل نمی‌شوند."/>
  <Card title="۱. ساخت و دانلود نسخه پشتیبان">
   <Space wrap style={{marginBottom:14}}>
    <Button type="primary" onClick={()=>void create()} loading={busy}>ساخت بکاپ جدید</Button>
    <Button onClick={()=>void reload()}>به‌روزرسانی وضعیت</Button>
    {status.has_key&&<a href={url('backup-secret.key')} download="backup-secret.key">
     <Button danger ghost>دانلود فایل کلید (محرمانه)</Button>
    </a>}
   </Space>
   <Alert type="warning" style={{marginBottom:12}} showIcon
    title="برای بازیابی به دو فایل نیاز دارید"
    description="بکاپ با AES-256 رمزگذاری می‌شود. فایل GPG و فایل کلید را جداگانه در محل امن نگه دارید. هرگز آن‌ها را در GitHub یا کانال تلگرام منتشر نکنید."/>
   <Table rowKey="name" size="small" pagination={{pageSize:6}}
    dataSource={status.backups} columns={[
    {title:'فایل بکاپ',dataIndex:'name',ellipsis:true},
    {title:'حجم',render:(_:unknown,r:BackupEntry)=>humanSize(r.size)},
    {title:'زمان ساخت (UTC)',dataIndex:'created'},
    {title:'دانلود',render:(_:unknown,r:BackupEntry)=><a href={url(r.name)} download={r.name}>
      <Button>دانلود بکاپ</Button>
     </a>}
   ]}/>
  </Card>
  <Card title="۲. بارگذاری و بررسی بکاپ روی سرور مقصد">
   <Space direction="vertical" style={{width:'100%'}}>
    <Space wrap align="start">
     <Upload beforeUpload={()=>false} maxCount={1} fileList={archive}
      onChange={i=>setArchive(i.fileList)} accept=".gpg,.gz" >
      <Button>انتخاب فایل بکاپ (.gpg)</Button>
     </Upload>
     <Upload beforeUpload={()=>false} maxCount={1} fileList={key}
      onChange={i=>setKey(i.fileList)}>
      <Button>انتخاب فایل کلید (.key)</Button>
     </Upload>
    </Space>
    <Button type="primary" loading={busy} disabled={!archive.length||!key.length}
      onClick={()=>void upload()}>آپلود و بررسی سلامت فایل</Button>
    {importID&&<Alert type={currentImport?.state==='failed'?'error':currentImport?.state==='done'?'success':'info'}
      title={'بررسی فایل واردشده: '+(currentImport?.state==='done'?'موفق':currentImport?.state==='failed'?'ناموفق':'در حال بررسی')}
      description={currentImport?.detail||'کد پیگیری: '+importID}/>}
   </Space>
  </Card>
  <Card title="۳. بازیابی روی این سرور">
   <Alert type="error" showIcon style={{marginBottom:12}}
    title="هشدار: بازیابی اطلاعات فعلی این سرور را جایگزین می‌کند"
    description="قبل از بازیابی از مقصد بکاپ محافظتی گرفته می‌شود. دامنه باید با دامنه بکاپ یکسان باشد. برای جلوگیری از اجرای هم‌زمان ربات، سرویس ربات در مقصد خاموش می‌ماند."/>
   {!restoreMode?
    <Button danger disabled={currentImport?.state!=='done'} onClick={()=>setRestoreMode(true)}>
     ادامه به مرحله تأیید بازیابی
    </Button>
    :<Space direction="vertical">
      <Typography.Text>برای تأیید، کلمه RESTORE را دقیقاً وارد کنید.</Typography.Text>
      <Input value={confirmation} onChange={e=>setConfirmation(e.target.value)} placeholder="RESTORE" dir="ltr"/>
      <Checkbox checked={acknowledged} onChange={e=>setAcknowledged(e.target.checked)}>
       می‌دانم داده‌های فعلی مقصد جایگزین می‌شوند و سرور قدیمی نباید هم‌زمان از ربات استفاده کند.
      </Checkbox>
      <Space>
       <Button danger type="primary" loading={busy}
        disabled={confirmation!=='RESTORE'||!acknowledged}
        onClick={()=>void restore()}>تأیید نهایی و شروع بازیابی</Button>
       <Button onClick={()=>setRestoreMode(false)}>انصراف</Button>
      </Space>
     </Space>}
  </Card>
  {error&&<Alert type="error" title={error}/>}
  <Card title="وضعیت عملیات‌های اخیر">
   <Table size="small" rowKey="job" pagination={false} dataSource={latestJob}
    columns={[
     {title:'شناسه',dataIndex:'job',ellipsis:true},
     {title:'وضعیت',render:(_:unknown,j:JobEntry)=>statusTag(j)},
     {title:'جزئیات',dataIndex:'detail'},
    ]}/>
  </Card>
 </Space>;
}
