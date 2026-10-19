import {useEffect,useState} from 'react';
import {Alert,App,Button,Card,Descriptions,Divider,Input,InputNumber,Modal,Select,Space,Switch,Table,Tag,Typography} from 'antd';
import {HttpUtil} from '@/utils';
type R=Record<string,any>;
const ops:Record<string,string>={points_add:'➕ افزایش امتیاز',points_sub:'➖ کاهش امتیاز',wallet_add:'💰 افزایش کیف پول',wallet_sub:'💸 کاهش کیف پول'};
const fmt=(n:any)=>Number(n||0).toLocaleString('fa-IR');
const date=(v:any)=>Number(v||0)>0?new Date(Number(v)*1000).toLocaleString('fa-IR'):'—';
const limit=(s:string)=>s.startsWith('points_')?1000000:1000000000;
const unit=(s:string)=>s.startsWith('points_')?'امتیاز':'تومان';
export default function ClubUserProfileModal({userId,onClose,onChanged,adminIds=[]}:{
 userId:number|null;onClose:()=>void;onChanged:()=>Promise<void>|void;adminIds?:number[];
}){
 const {message,modal}=App.useApp();
 const [item,setItem]=useState<R|null>(null);
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 const [operation,setOperation]=useState('');
 const [amount,setAmount]=useState<number|null>(null);
 const [note,setNote]=useState('');
 const [history,setHistory]=useState<R[]>([]);
 const [messages,setMessages]=useState<R[]>([]);
 const [openChat,setOpenChat]=useState<number|null>(null);
 const [destination,setDestination]=useState<number|null>(null);
 const call=async(action:string,details:R={})=>{
  const r=await HttpUtil.post('/panel/api/sales-bot',{action,...details},
   {headers:{'Content-Type':'application/json'},silent:true,timeout:65000});
  if(!r?.success)throw new Error(r?.msg||'درخواست مدیریت اجرا نشد');
  return r.obj as any;
 };
 const reload=async(uid:number)=>{
  try{
   const res=await call('social_admin',{view:'user_detail',id:uid});
   setItem(res);setError('');
   setHistory(await call('social_admin',{view:'adjustments',code:res.code})||[]);
  }catch(e){setError(String(e));}
 };
 useEffect(()=>{
  setItem(null);setHistory([]);setError('');setOperation('');setAmount(null);
  setOpenChat(null);setMessages([]);setDestination(adminIds.length?adminIds[0]:null);
  if(userId!=null)void reload(userId);
 },[userId]);
 const apply=()=>{
  if(!item||!operation||amount===null||!Number.isSafeInteger(amount)||amount<=0||amount>limit(operation)){
   message.error('عملیات و مقدار صحیح مثبت انتخاب کنید');return;
  }
  const key=operation.startsWith('points_')?'points_balance':'wallet_balance';
  const before=Number(item[key]||0),after=before+(operation.endsWith('_sub')?-amount:amount);
  if(after<0){message.error('موجودی برای کاهش کافی نیست');return;}
  const requestId=crypto.randomUUID().replace(/-/g,'');
  const target=item;const selected=operation;const value=amount;const description=note.trim();
  modal.confirm({title:'تأیید نهایی '+ops[selected],okText:'تأیید و ثبت تغییر',
   cancelText:'انصراف',okButtonProps:{danger:selected.endsWith('_sub')},
   content:<Space direction="vertical" size={6}>
    <Typography.Text>کاربر: {target.display_name||target.name} ({target.code})</Typography.Text>
    <Typography.Text>مقدار: {fmt(value)} {unit(selected)}</Typography.Text>
    <Typography.Text>موجودی قبلی: {fmt(before)} {unit(selected)}</Typography.Text>
    <Typography.Text strong>موجودی جدید: {fmt(after)} {unit(selected)}</Typography.Text>
    {description&&<Typography.Text>توضیح: {description}</Typography.Text>}
    <Typography.Text type="secondary">اعلان این عملیات برای کاربر در صف تلگرام ثبت می‌شود.</Typography.Text>
   </Space>,
   onOk:async()=>{
    setBusy(true);
    try{
     const result=await call('social_adjust',{code:target.code,operation:selected,amount:value,
       request_id:requestId,note:description});
     message.success(result?.applied?'تغییر ثبت و اعلان کاربر در صف قرار گرفت':'درخواست پیش‌تر اعمال شده بود');
     await Promise.all([reload(target.id),Promise.resolve(onChanged())]);
     setOperation('');setAmount(null);setNote('');
    }catch(e){message.error(String(e));throw e;}
    finally{setBusy(false);}
   }
  });
 };
 const toggle=async(field:'blocked'|'reseller',value:boolean)=>{
  if(!item)return;
  setBusy(true);
  try{
   await call('user',{id:item.id,blocked:field==='blocked'?value:!!item.blocked,
       reseller:field==='reseller'?value:!!item.reseller});
   await Promise.all([reload(item.id),Promise.resolve(onChanged())]);
   message.success('وضعیت کاربر ذخیره شد');
  }catch(e){message.error(String(e));}
  finally{setBusy(false);}
 };
 const showChat=async(id:number)=>{
  setOpenChat(id);setMessages([]);
  try{setMessages(await call('social_admin',{view:'messages',id})||[]);}
  catch(e){message.error(String(e));}
 };
 const sendGallery=async()=>{
  if(!item||destination===null){message.warning('مدیر دریافت‌کننده را انتخاب کنید');return;}
  setBusy(true);
  try{
   const a=await call('social_gallery_to_admin',{id:item.id,admin_id:destination});
   message.success((a?.count||0)+' تصویر در صف ارسال به تلگرام مدیر قرار گرفت');
  }catch(e){message.error(String(e));}
  finally{setBusy(false);}
 };
 const tiny=(rows:R[],cols:any[],key='id')=><Table size="small" rowKey={(x:R)=>String(x[key]??x.created??x.body)}
  dataSource={rows||[]} columns={cols} pagination={{pageSize:5}} scroll={{x:480}}/>;
 const tagNames:Record<string,string>={followers:'دنبال‌کنندگان',following:'دنبال می‌کند',
  profile_views:'بازدید',profile_likes:'لایک',profile_dislikes:'دیسلایک',
  profile_comments:'نظرات',music:'موزیک',films:'فیلم و سریال',playlists:'پلی‌لیست',
  chats:'گفتگوها',reports:'گزارش',orders:'سفارش',services:'اشتراک'};
 return <>
 <Modal open={userId!=null} onCancel={()=>{if(!busy)onClose();}}
  title="👤 پرونده کامل کاربر و مدیریت کلاب ناشناس"
  footer={<Button onClick={onClose} disabled={busy}>بستن</Button>} destroyOnHidden width={1060}>
  {error&&<Alert type="error" title={error} style={{marginBottom:10}}/>}
  {!item&&!error&&<Typography.Text type="secondary">در حال خواندن اطلاعات کاربر…</Typography.Text>}
  {item&&<Space direction="vertical" style={{width:'100%'}} size="middle">
   <Card size="small" title="حساب تلگرام، اشتراک و مدیریت">
    <Descriptions bordered column={{xs:1,sm:2}} size="small">
     <Descriptions.Item label="نام حساب">{item.name||'نامشخص'}</Descriptions.Item>
     <Descriptions.Item label="نام کلاب">{item.display_name||'تکمیل نشده'}</Descriptions.Item>
     <Descriptions.Item label="شناسه عمومی">{item.code}</Descriptions.Item>
     <Descriptions.Item label="شناسه عددی تلگرام (فقط مدیر)">{item.id}</Descriptions.Item>
     <Descriptions.Item label="یوزرنیم تلگرام">{item.telegram_username?'@'+item.telegram_username:'ثبت نشده'}</Descriptions.Item>
     <Descriptions.Item label="شماره تماس">{item.phone||'ثبت نشده'}</Descriptions.Item>
     <Descriptions.Item label="زمان عضویت">{date(item.created)}</Descriptions.Item>
     <Descriptions.Item label="معرف">{item.referrer||'بدون معرف'}</Descriptions.Item>
     <Descriptions.Item label="امتیاز">⭐ {fmt(item.points_balance)}</Descriptions.Item>
     <Descriptions.Item label="کیف پول">💰 {fmt(item.wallet_balance)} تومان</Descriptions.Item>
     <Descriptions.Item label="اشتراک‌های فعال">{item.stats?.services??0}</Descriptions.Item>
     <Descriptions.Item label="کل سفارش‌ها">{item.stats?.orders??0}</Descriptions.Item>
    </Descriptions>
    <Space wrap style={{marginTop:14}}>
     <Typography.Text>مسدود:</Typography.Text>
     <Switch disabled={busy} checked={!!item.blocked} onChange={v=>void toggle('blocked',v)}/>
     <Typography.Text>نماینده:</Typography.Text>
     <Switch disabled={busy} checked={!!item.reseller} onChange={v=>void toggle('reseller',v)}/>
     <Button onClick={()=>void reload(item.id)}>بازخوانی اطلاعات</Button>
    </Space>
   </Card>
   <Card size="small" title="🌐 پروفایل کلاب ناشناس">
    <Alert type={item.profile?.completed?'success':'warning'} showIcon
     title={item.profile?.completed?'✅ پروفایل تکمیل شده':'⚠️ پروفایل هنوز کامل نشده'}
     description={item.profile?.completed?'تمام فیلدهای اجباری تکمیل شده‌اند':
      'فیلدهای ناقص: '+(item.profile?.missing||[]).join('، ')}/>
    <Descriptions bordered column={{xs:1,sm:2}} size="small" style={{marginTop:12}}>
     <Descriptions.Item label="سن">{item.profile?.age||'تعیین نشده'}</Descriptions.Item>
     <Descriptions.Item label="جنسیت">{item.profile?.gender||'تعیین نشده'}</Descriptions.Item>
     <Descriptions.Item label="استان">{item.profile?.province||'تعیین نشده'}</Descriptions.Item>
     <Descriptions.Item label="علاقه‌مندی‌ها">{(item.profile?.interests||[]).join('، ')||'ثبت نشده'}</Descriptions.Item>
     <Descriptions.Item label="عکس‌ها">{(item.profile?.photos||[]).length} / ۵</Descriptions.Item>
     <Descriptions.Item label="نمایش پروفایل">{item.profile?.visibility||'—'}</Descriptions.Item>
     <Descriptions.Item label="قابل جستجو">{item.profile?.discoverable?'بله':'خیر'}</Descriptions.Item>
     <Descriptions.Item label="نمایش بازدید">{item.profile?.show_visitors?'بله':'خیر'}</Descriptions.Item>
     <Descriptions.Item label="زمان ویرایش">{date(item.profile?.updated)}</Descriptions.Item>
     <Descriptions.Item label="لوکیشن ثبت‌شده">{item.location?
       String(item.location.lat)+', '+String(item.location.lon):'ثبت نشده'}</Descriptions.Item>
    </Descriptions>
    {(item.profile?.photos||[]).length>0&&<Space wrap style={{marginTop:12}}>
      <Typography.Text>تصاویر در سرور تلگرام نگهداری می‌شوند.</Typography.Text>
      <Select style={{width:230}} value={destination}
        placeholder="مدیر دریافت‌کننده" options={adminIds.map(id=>({value:id,label:'مدیر تلگرام '+id}))}
        onChange={setDestination}/>
      <Button loading={busy} onClick={()=>void sendGallery()}>🖼 ارسال گالری به تلگرام مدیر</Button>
    </Space>}
    <Divider/>
    <Space wrap>
      {Object.entries(item.stats||{}).map(([key,value])=><Tag key={key}>{tagNames[key]||key}: {value as number}</Tag>)}
    </Space>
    {!!item.medals?.length&&<Typography.Paragraph style={{marginTop:12}}>
      🏆 مدال‌ها: {item.medals.join(' | ')}
    </Typography.Paragraph>}
    <Divider/>
    <Typography.Text strong>موزیک و فیلم‌های منتشرشده</Typography.Text>
    {tiny(item.recent_media||[],[
     {title:'عنوان',dataIndex:'title'},{title:'نوع',dataIndex:'kind'},
     {title:'وضعیت',dataIndex:'status'},{title:'تاریخ',render:(_:any,r:R)=>date(r.created)}])}
    <Typography.Text strong>پلی‌لیست‌های کاربر</Typography.Text>
    {tiny(item.recent_playlists||[],[
     {title:'عنوان',dataIndex:'name'},{title:'نوع',dataIndex:'kind'},
     {title:'وضعیت',render:(_:any,r:R)=>r.removed?'حذف‌شده':r.private?'خصوصی':'عمومی'}])}
    <Typography.Text strong>نظرات عمومی روی پروفایل</Typography.Text>
    {tiny(item.recent_comments||[],[
     {title:'نویسنده',render:(_:any,r:R)=>r.name||r.public_code||'کاربر'},
     {title:'پیام',dataIndex:'body'},{title:'زمان',render:(_:any,r:R)=>date(r.created)}])}
   </Card>
   <Card size="small" title="⭐💰 مدیریت امتیاز و کیف پول">
    <Space wrap>
     {Object.entries(ops).map(([op,name])=><Button key={op}
       type={operation===op?'primary':'default'} danger={op.endsWith('_sub')}
       onClick={()=>{setOperation(op);setAmount(null);setNote('');}}>{name}</Button>)}
    </Space>
    {operation&&<Space direction="vertical" size="middle" style={{width:'100%',marginTop:16}}>
     <Typography.Text>مقدار به {unit(operation)}</Typography.Text>
     <InputNumber min={1} max={limit(operation)} precision={0} value={amount}
      onChange={setAmount} style={{width:'100%'}}/>
     <Input.TextArea rows={2} maxLength={200} showCount value={note}
      placeholder="دلیل یا توضیح برای کاربر (اختیاری)" onChange={e=>setNote(e.target.value)}/>
     <Button type="primary" danger={operation.endsWith('_sub')} loading={busy}
      onClick={apply}>بررسی موجودی و تأیید نهایی</Button>
    </Space>}
    <Divider/>
    <Typography.Text strong>تاریخچه عملیات مدیر</Typography.Text>
    {tiny(history,[
     {title:'نوع',render:(_:any,r:R)=>ops[r.operation]||r.operation},
     {title:'مقدار',render:(_:any,r:R)=>fmt(r.amount)},
     {title:'قبل / بعد',render:(_:any,r:R)=>fmt(r.before_balance)+' ← '+fmt(r.after_balance)},
     {title:'توضیح',dataIndex:'note'},
     {title:'زمان',render:(_:any,r:R)=>date(r.created)}
    ],'request_id')}
   </Card>
   <Card size="small" title="سفارش‌ها، خدمات و تراکنش‌ها">
    <Typography.Text strong>اشتراک‌های اخیر</Typography.Text>
    {tiny(item.recent_services||[],[
     {title:'پروتکل',dataIndex:'protocol'},{title:'پلن',dataIndex:'plan'},
     {title:'انقضا',render:(_:any,r:R)=>date(r.expires)},
     {title:'حذف‌شده',render:(_:any,r:R)=>r.deleted?'بله':'خیر'}])}
    <Typography.Text strong>سفارش‌های اخیر</Typography.Text>
    {tiny(item.recent_orders||[],[
     {title:'پلن',dataIndex:'plan'},{title:'قیمت',render:(_:any,r:R)=>fmt(r.price)},
     {title:'وضعیت',dataIndex:'status'},{title:'زمان',render:(_:any,r:R)=>date(r.created)}])}
    <Typography.Text strong>گردش کیف پول</Typography.Text>
    {tiny(item.recent_wallet||[],[
     {title:'مبلغ',render:(_:any,r:R)=>fmt(r.amount)},
     {title:'علت',dataIndex:'reason'},{title:'تاریخ',render:(_:any,r:R)=>date(r.created)}])}
    <Typography.Text strong>گردش امتیاز</Typography.Text>
    {tiny(item.recent_points||[],[
     {title:'امتیاز',render:(_:any,r:R)=>fmt(r.points)},
     {title:'علت',dataIndex:'kind'},{title:'تاریخ',render:(_:any,r:R)=>date(r.created)}])}
   </Card>
   <Card size="small" title="💬 گفتگوها و گزارش‌ها">
    {tiny(item.recent_chats||[],[
     {title:'گفتگو',dataIndex:'id'},{title:'نوع',dataIndex:'type'},
     {title:'شروع',render:(_:any,r:R)=>date(r.created)},
     {title:'وضعیت',render:(_:any,r:R)=>r.closed?'پایان‌یافته':'فعال'},
     {title:'مدیریت',render:(_:any,r:R)=><Button onClick={()=>void showChat(r.id)}>مشاهده گفتگو</Button>}
    ])}
   </Card>
  </Space>}
 </Modal>
 <Modal open={openChat!==null} onCancel={()=>setOpenChat(null)}
  footer={<Button onClick={()=>setOpenChat(null)}>بستن</Button>}
  title={'🔐 آرشیو گفتگوی '+(openChat||'')} width={680}>
  {messages.length===0?<Typography.Text type="secondary">پیامی ثبت نشده است.</Typography.Text>:
   messages.map((m:R)=><div key={m.id} style={{marginBottom:9,padding:10,borderBottom:'1px solid #ddd'}}>
    <Typography.Text strong>{m.display_name||m.public_code||'کاربر'}</Typography.Text>
    <Typography.Text type="secondary"> · {date(m.created)}</Typography.Text>
    <Typography.Paragraph style={{whiteSpace:'pre-wrap',margin:0}}>{m.body}</Typography.Paragraph>
   </div>)}
 </Modal>
 </>;
}
