import {useEffect,useMemo,useState} from 'react';
import {Alert,Button,Card,Checkbox,Input,Space,Table,Tag,Typography,Upload,App,Descriptions} from 'antd';
import type {UploadFile} from 'antd';
import {HttpUtil} from '@/utils';
import {httpRequest} from '@/api/http-init';

type BackupEntry={name:string,size:number,created:string};
type BackupMetadata={
 source_domain:string;destination_domain:string;requires_rebind:boolean;
 created_utc?:string;snapshot_type?:string;media_included:boolean;
 database_tables:Record<string,number>;files:number;safe_portable_restore:boolean;
};
type JobEntry={job:string,state:string,detail?:string,at?:number,action?:string,metadata?:BackupMetadata};
type BackupStatus={backups:BackupEntry[],jobs:JobEntry[],has_key:boolean};
const endpoint='/panel/api/masiha-backup';
const humanSize=(n:number)=>n>=1073741824?(n/1073741824).toFixed(2)+' GB':
 n>=1048576?(n/1048576).toFixed(1)+' MB':(n/1024).toFixed(0)+' KB';

export default function BackupRestorePanel(){
 const {message}=App.useApp();
 const [status,setStatus]=useState<BackupStatus>({backups:[],jobs:[],has_key:false});
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 const [includeMedia,setIncludeMedia]=useState(true);
 const [archive,setArchive]=useState<UploadFile[]>([]);
 const [key,setKey]=useState<UploadFile[]>([]);
 const [importID,setImportID]=useState('');
 const [confirmation,setConfirmation]=useState('');
 const [acknowledged,setAcknowledged]=useState(false);
 const [restoreMode,setRestoreMode]=useState(false);
 const recent=useMemo(()=>[...status.jobs].sort((a,b)=>(b.at||0)-(a.at||0)).slice(0,9),[status.jobs]);
 // The verified upload can still be selected after a browser refresh.
 const verifiedUpload=recent.find(j=>j.action==='inspect'&&j.state==='done'&&j.metadata);
 const selectedJob=importID||verifiedUpload?.job||'';
 const currentImport=recent.find(j=>j.job===selectedJob);
 const meta=currentImport?.metadata;
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
   const r=await HttpUtil.post(endpoint+'/create',{include_media:includeMedia},
    {headers:{'Content-Type':'application/json'},silent:true,timeout:15000});
   if(!r.success)throw new Error(r.msg||'ساخت بکاپ آغاز نشد');
   message.success('ساخت بکاپ آغاز شد. پس از تکمیل، فایل رمزگذاری‌شده و کلید را جداگانه دانلود کنید.');
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
   const r=await httpRequest('POST',endpoint+'/import',form,{timeout:3600000});
   const data=r.data as {success:boolean,msg?:string,obj?:{job:string}};
   if(!data?.success||!data.obj?.job)throw new Error(data?.msg||'آپلود ناموفق');
   setImportID(data.obj.job);
   message.success('فایل رسید؛ بررسی رمزگشایی، دامنه مبدأ و سازگاری شروع شد.');
   await reload();
  }catch(e){message.error(String(e))}
  finally{setBusy(false)}
 };
 const restore=async()=>{
  if(!selectedJob||currentImport?.state!=='done'||confirmation!=='RESTORE'||!acknowledged)return;
  setBusy(true);
  try{
   const r=await HttpUtil.post(endpoint+'/restore',
    {job:selectedJob,confirm:confirmation},
    {headers:{'Content-Type':'application/json'},silent:true,timeout:15000});
   if(!r.success)throw new Error(r.msg||'بازیابی آغاز نشد');
   message.warning('بازیابی قابل‌انتقال آغاز شد؛ آدرس، SSL و پنل مقصد حفظ می‌شوند. فعال‌سازی ربات نیاز به انتقال نهایی دارد.');
   setRestoreMode(false);setConfirmation('');setAcknowledged(false);
  }catch(e){message.error(String(e))}
  finally{setBusy(false)}
 };
 const statusTag=(job:JobEntry)=>{
  const labels:Record<string,[string,string]>={running:['blue','در حال اجرا'],done:['green','موفق'],failed:['red','خطا']};
  const [color,title]=labels[job.state]||['default',job.state];
  return <Tag color={color}>{title}</Tag>;
 };
 return <Space direction="vertical" size="large" style={{width:'100%'}}>
  <Alert type="info" showIcon title="بکاپ قابل‌انتقال مسیحا — حتی با تغییر دامنه یا IP"
   description="در سرور مقصد، دامنه، SSL، مسیر پنل و نسخه نصب‌شده حفظ می‌شوند؛ داده‌های کاربران، مصرف، سفارش‌ها و تنظیمات قابل‌انتقال از فایل بکاپ خوانده می‌شوند. پورت‌های تکراری نگاشت می‌شوند و نودهای خارجی تا بررسی مجدد غیرفعال می‌مانند."/>
  <Card title="۱. ساخت بکاپ و دریافت فایل‌ها">
   <Space direction="vertical" style={{width:'100%'}} size="middle">
    <Checkbox checked={includeMedia} onChange={e=>setIncludeMedia(e.target.checked)}>
     بکاپ کامل: شامل رسانه‌ها، صف‌های ارسال و کش Local Telegram Bot API
    </Checkbox>
    <Space wrap>
     <Button type="primary" onClick={()=>void create()} loading={busy}>
      {includeMedia?'ساخت بکاپ کامل':'ساخت بکاپ سبک (بدون رسانه)'}
     </Button>
     <Button onClick={()=>void reload()}>به‌روزرسانی</Button>
     {status.has_key&&<a href={url('backup-secret.key')} download="backup-secret.key">
      <Button danger ghost>دانلود کلید رمزگشایی (محرمانه)</Button>
     </a>}
    </Space>
    <Alert type="warning" showIcon
     title="فایل بکاپ و کلید، هر دو الزامی‌اند"
     description="فایل GPG با AES-256 رمزگذاری می‌شود. کلید را جداگانه و محرمانه نگه دارید. ساخت بکاپ کامل به فضای کافی نیاز دارد. بکاپ هنگام فعال بودن سرور یک Snapshot است؛ قبل از جابه‌جایی ربات باید آخرین تغییرات هم همگام شوند."/>
    <Table rowKey="name" size="small" pagination={{pageSize:6}}
     dataSource={status.backups} columns={[
      {title:'فایل بکاپ',dataIndex:'name',ellipsis:true},
      {title:'حجم',render:(_:unknown,r:BackupEntry)=>humanSize(r.size)},
      {title:'زمان ساخت (UTC)',dataIndex:'created'},
      {title:'دانلود',render:(_:unknown,r:BackupEntry)=><a href={url(r.name)} download={r.name}>
       <Button>دانلود بکاپ</Button>
      </a>},
     ]}/>
   </Space>
  </Card>
  <Card title="۲. وارد کردن بکاپ روی سرور جدید">
   <Space direction="vertical" style={{width:'100%'}}>
    <Typography.Text type="secondary">پس از نصب نسخه جدید مسیحا با دامنه مقصد، فایل GPG و کلید آن را همین‌جا بارگذاری کن (حداکثر ۶ گیگابایت).</Typography.Text>
    <Space wrap align="start">
     <Upload beforeUpload={()=>false} maxCount={1} fileList={archive}
      onChange={i=>setArchive(i.fileList)} accept=".gpg">
      <Button>انتخاب فایل بکاپ (.gpg)</Button>
     </Upload>
     <Upload beforeUpload={()=>false} maxCount={1} fileList={key}
      onChange={i=>setKey(i.fileList)} accept=".key,.txt">
      <Button>انتخاب کلید رمزگشایی</Button>
     </Upload>
    </Space>
    <Button type="primary" loading={busy} disabled={!archive.length||!key.length}
     onClick={()=>void upload()}>بارگذاری، اعتبارسنجی و تشخیص آدرس جدید</Button>
    {selectedJob&&<Alert
     type={currentImport?.state==='failed'?'error':currentImport?.state==='done'?'success':'info'}
     title={'وضعیت اعتبارسنجی: '+(currentImport?.state==='done'?'موفق':currentImport?.state==='failed'?'ناموفق':'در حال بررسی')}
     description={currentImport?.detail||'شناسه: '+selectedJob}/>}
    {meta&&<Descriptions size="small" bordered column={1}>
     <Descriptions.Item label="دامنه بکاپ">{meta.source_domain}</Descriptions.Item>
     <Descriptions.Item label="دامنه مقصد">{meta.destination_domain}</Descriptions.Item>
     <Descriptions.Item label="تغییر آدرس">{meta.requires_rebind?'بله — بازیابی با آدرس مقصد':'خیر'}</Descriptions.Item>
     <Descriptions.Item label="محتوای رسانه">{meta.media_included?'شامل رسانه':'بدون رسانه'}</Descriptions.Item>
     <Descriptions.Item label="تعداد فایل‌ها">{meta.files}</Descriptions.Item>
    </Descriptions>}
   </Space>
  </Card>
  <Card title="۳. بازیابی با حفظ هویت سرور جدید">
   <Alert type="warning" showIcon style={{marginBottom:12}}
    title="بازیابی محافظت‌شده؛ بازگشت‌پذیر"
    description="اطلاعات کاربران، ربات و گواهی‌های VPN از بکاپ بازیابی می‌شوند. دامنه، گواهی HTTPS، Nginx، رمز مدیریت پنل مقصد و باینری جدید حفظ خواهند شد. نود خارجی و پردازشگرهای تلگرام پس از بازیابی خودکار فعال نمی‌شوند. فایل‌ها و دیتابیس‌های فعلی مقصد نسخه بازگشت خواهند داشت."/>
   {!restoreMode?
    <Button danger disabled={currentImport?.state!=='done'||!meta} onClick={()=>setRestoreMode(true)}>
     ادامه به تأیید بازیابی روی این سرور
    </Button>
    :<Space direction="vertical">
     <Typography.Text>برای تأیید، کلمه RESTORE را دقیقاً وارد کنید.</Typography.Text>
     <Input value={confirmation} onChange={e=>setConfirmation(e.target.value)} placeholder="RESTORE" dir="ltr"/>
     <Checkbox checked={acknowledged} onChange={e=>setAcknowledged(e.target.checked)}>
      می‌دانم که داده‌های مقصد جایگزین می‌شوند؛ ربات مبدأ باید پیش از فعال‌سازی ربات مقصد متوقف شده باشد.
     </Checkbox>
     <Space>
      <Button danger type="primary" loading={busy}
       disabled={confirmation!=='RESTORE'||!acknowledged}
       onClick={()=>void restore()}>شروع بازیابی قابل‌انتقال</Button>
      <Button onClick={()=>setRestoreMode(false)}>انصراف</Button>
     </Space>
    </Space>}
  </Card>
  {error&&<Alert type="error" title={error}/>}
  <Card title="وضعیت عملیات‌ها">
   <Table size="small" rowKey="job" pagination={false} dataSource={recent}
    columns={[
     {title:'عملیات',render:(_:unknown,j:JobEntry)=>j.action||'بازیابی / بکاپ'},
     {title:'وضعیت',render:(_:unknown,j:JobEntry)=>statusTag(j)},
     {title:'توضیحات',dataIndex:'detail'},
    ]}/>
  </Card>
 </Space>;
}
