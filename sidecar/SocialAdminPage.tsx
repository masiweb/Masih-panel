import {useEffect,useState} from 'react';
import {Alert,App,Button,Card,Form,Input,InputNumber,Modal,Space,Table,Tag,Typography} from 'antd';
import {HttpUtil} from '@/utils';
type Row=Record<string,any>;
const labels:Record<string,string>={
  social_post_music:'🎵 امتیاز ارسال موزیک',
  social_post_movie:'🎬 امتیاز ارسال فیلم',
  social_post_series:'📺 امتیاز ارسال هر قسمت سریال',
  social_post_playlist:'📚 امتیاز ساخت پلی‌لیست',
  social_like_music:'❤️ امتیاز هر لایک موزیک',
  social_like_movie:'❤️ امتیاز هر لایک فیلم',
  social_like_series:'❤️ امتیاز هر لایک قسمت سریال',
  social_like_playlist:'❤️ امتیاز هر لایک پلی‌لیست',
  social_like_profile:'❤️ امتیاز هر لایک پروفایل',
  social_chat_retention_days:'🗄 مدت نگهداری متن گفتگوها (روز)'
};
const chatCosts:Record<string,string>={
 social_chat_cost_random:'🎲 چت تصادفی',
 social_chat_cost_filtered:'🎯 چت با فیلتر',
 social_chat_cost_search:'🔎 چت از جستجوی کاربر',
 social_chat_cost_near:'📍 چت از کاربران نزدیک',
 social_chat_cost_following:'👀 چت از «دنبال می‌کنم»',
 social_chat_cost_followers:'👥 چت از «دنبال‌کنندگان»',
 social_chat_cost_direct:'💬 چت از لینک یا پروفایل مستقیم'
};
const adjustLabels:Record<string,string>={
  points_add:'⭐ افزایش امتیاز',
  points_sub:'⭐ کاهش امتیاز',
  wallet_add:'💰 افزایش کیف پول',
  wallet_sub:'💸 کاهش کیف پول'
};
const adjustLimits:Record<string,number>={points_add:1000000,points_sub:1000000,
  wallet_add:1000000000,wallet_sub:1000000000};
export default function SocialAdminPage(){
  const {message,modal}=App.useApp();
  const [stats,setStats]=useState<Row>({});
  const [profiles,setProfiles]=useState<Row[]>([]);
  const [chats,setChats]=useState<Row[]>([]);
  const [reports,setReports]=useState<Row[]>([]);
  const [messages,setMessages]=useState<Row[]>([]);
  const [selected,setSelected]=useState<Row|null>(null);
  const [editingUser,setEditingUser]=useState<Row|null>(null);
  const [editOperation,setEditOperation]=useState('');
  const [editAmount,setEditAmount]=useState<number|null>(null);
  const [editNote,setEditNote]=useState('');
  const [accountHistory,setAccountHistory]=useState<Row[]>([]);
  const [accountBusy,setAccountBusy]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [form]=Form.useForm();
  const [chatForm]=Form.useForm();
  const call=async(action:string, values:Row={})=>{
    const r=await HttpUtil.post('/panel/api/sales-bot',{action,...values},
      {headers:{'Content-Type':'application/json'},silent:true,timeout:65000});
    if(!r?.success)throw new Error(r?.msg||'خطای ارتباط با مدیریت اجتماعی');
    return r.obj;
  };
  const reload=async()=>{
    try{
      const [a,b,c,d]=await Promise.all([
        call('social_admin',{view:'overview'}),call('social_admin',{view:'users'}),
        call('social_admin',{view:'chats'}),call('social_admin',{view:'reports'})
      ]);
      setStats(a||{});setProfiles(b||[]);setChats(c||[]);setReports(d||[]);
      form.setFieldsValue(a?.rewards||{});
      chatForm.setFieldsValue(a?.rewards||{});
      setError('');
    }catch(e){setError(String(e));}
  };
  useEffect(()=>{void reload();},[]);
  const save=async(values:Row)=>{
    setBusy(true);
    try{await call('media_settings',{values});message.success('قوانین امتیاز ثبت شد');await reload();}
    catch(e){message.error(String(e));}
    finally{setBusy(false);}
  };
  const openAccount=async(row:Row)=>{
    setEditingUser(row);setEditOperation('');setEditAmount(null);setEditNote('');
    setAccountHistory([]);
    try{setAccountHistory((await call('social_admin',{view:'adjustments',code:row.code}))||[]);}
    catch(e){message.error(String(e));}
  };
  const confirmAdjustment=()=>{
    if(!editingUser||!editOperation||editAmount===null||!Number.isSafeInteger(editAmount)||
       editAmount<1||editAmount>adjustLimits[editOperation]){
      message.error('مقدار صحیح و مثبت وارد کنید.');return;
    }
    const key=editOperation.startsWith('points_')?'points_balance':'wallet_balance';
    const before=Number(editingUser[key]??0);
    const after=before+(editOperation.endsWith('_sub')?-editAmount:editAmount);
    if(after<0){message.error('موجودی برای انجام این کاهش کافی نیست.');return;}
    const unit=editOperation.startsWith('points_')?'امتیاز':'تومان';
    const requestId=crypto.randomUUID().replace(/-/g,'');
    const current=editingUser;
    const operation=editOperation;
    const amount=editAmount;
    const note=editNote.trim();
    modal.confirm({
      title:'تأیید نهایی: '+adjustLabels[operation],
      content:<Space direction="vertical" size="small">
        <Typography.Text>کاربر: {current.display_name||current.name||'کاربر'} ({current.code})</Typography.Text>
        <Typography.Text>مقدار: {amount.toLocaleString('fa-IR')} {unit}</Typography.Text>
        <Typography.Text>موجودی قبلی: {before.toLocaleString('fa-IR')} {unit}</Typography.Text>
        <Typography.Text strong>موجودی پس از اعمال: {after.toLocaleString('fa-IR')} {unit}</Typography.Text>
        {note&&<Typography.Text>توضیح: {note}</Typography.Text>}
        <Typography.Text type="secondary">پس از اعمال، پیام تغییر موجودی برای کاربر در صف ارسال قرار می‌گیرد.</Typography.Text>
      </Space>,
      okText:'تأیید و ثبت تغییر',cancelText:'انصراف',okButtonProps:{danger:operation.endsWith('_sub')},
      onOk:async()=>{
        setAccountBusy(true);
        try{
          const result=await call('social_adjust',{code:current.code,operation,amount,request_id:requestId,note});
          message.success(result?.applied?'عملیات ثبت شد و اعلان کاربر در صف قرار گرفت.':'درخواست قبلاً ثبت شده بود؛ دوباره اعمال نشد.');
          const rows=(await call('social_admin',{view:'users'}))||[];
          setProfiles(rows);
          setEditingUser(rows.find((u:Row)=>u.code===current.code)||null);
          setAccountHistory((await call('social_admin',{view:'adjustments',code:current.code}))||[]);
          setEditAmount(null);setEditNote('');setEditOperation('');
        }catch(e){message.error(String(e));throw e;}
        finally{setAccountBusy(false);}
      }
    });
  };
  const inspect=async(chat:Row)=>{
    setSelected(chat);setMessages([]);
    try{setMessages((await call('social_admin',{view:'messages',id:chat.id}))||[]);}
    catch(e){message.error(String(e));}
  };
  const review=async(id:number,decision:string)=>{
    try{await call('social_report',{id,decision});message.success('گزارش بررسی شد');await reload();}
    catch(e){message.error(String(e));}
  };
  const fmt=(v:number)=>v?new Date(v*1000).toLocaleString('fa-IR'):'—';
  return <Space direction="vertical" style={{width:'100%'}} size="large">
    <Alert type="warning" showIcon
      title="حریم خصوصی و دسترسی مدیریت"
      description="شناسه عددی و شماره تلفن فقط در این صفحه مدیریتی نمایش داده می‌شوند. گفتگوها در ربات منتقل و با اطلاع شرکت‌کنندگان ثبت می‌شوند؛ این چت‌ها رمزنگاری سرتاسری ندارند. به حساب مدیر دسترسی محدود بدهید."/>
    {error&&<Alert type="error" title={error}/>}
    <Space wrap>
      <Tag>پروفایل‌ها: {stats.profiles??0}</Tag>
      <Tag color="green">کامل: {stats.completed??0}</Tag>
      <Tag color="blue">چت‌های فعال: {stats.active_chats??0}</Tag>
      <Tag>گفتگوهای ثبت‌شده: {stats.chat_total??0}</Tag>
      <Tag>پیام‌های ذخیره‌شده: {stats.messages??0}</Tag>
      <Tag color="red">گزارش‌های باز: {stats.pending_reports??0}</Tag>
      <Tag>موقعیت‌های فعال: {stats.geo_optins??0}</Tag>
      <Button onClick={()=>void reload()}>بارگذاری مجدد</Button>
    </Space>
    <Card title="⭐ تعرفه شروع گفتگو در کلاب ناشناس" size="small">
      <Alert type="info" showIcon style={{marginBottom:16}}
        title="فقط شروع موفق هر گفتگو هزینه دارد"
        description="مبلغ‌ها برحسب ستاره/امتیاز داخلی ربات هستند. مشاهده فهرست کاربران و جستجو رایگان است. برای چت تصادفی پس از پیدا شدن هم‌صحبت، و برای چت درخواستی فقط بعد از پذیرش درخواست، یک‌بار کسر می‌شود. پیام‌های بعدی رایگان هستند. ورود مستقیم به پروفایل حداقل با بیشترین تعرفه مسیرهای جستجو حساب می‌شود. صفر یعنی رایگان."/>
      <Form form={chatForm} layout="vertical" onFinish={v=>void save(v)}>
        <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(210px,1fr))',gap:12}}>
          {Object.entries(chatCosts).map(([key,label])=><Form.Item key={key} name={key}
            label={label}
            rules={[{required:true,message:'مقدار هزینه را وارد کنید'}]}>
            <InputNumber min={0} max={10000} precision={0} style={{width:'100%'}}/>
          </Form.Item>)}
        </div>
        <Button loading={busy} type="primary" htmlType="submit">ذخیره تعرفه شروع چت‌ها</Button>
      </Form>
    </Card>
    <Card title="⚙️ امتیاز ثبت رسانه و لایک کاربران" size="small">
      <Form form={form} layout="vertical" onFinish={v=>void save(v)}>
        <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(190px,1fr))',gap:12}}>
          {Object.entries(labels).map(([key,label])=><Form.Item key={key} name={key} label={label}
            rules={[{required:true,message:'مقدار امتیاز را وارد کنید'}]}>
            <InputNumber min={key==='social_chat_retention_days'?30:0} max={key==='social_chat_retention_days'?365:10000} precision={0} style={{width:'100%'}}/>
          </Form.Item>)}
        </div>
        <Alert type="info" showIcon style={{marginBottom:14}}
          description="امتیاز فقط برای محتوای تازه منتشرشده و لایک معتبر ثبت می‌شود. دیسلایک امتیاز مثبت ندارد. پرداخت امتیاز با مرجع یکتا و سقف ضدتقلب انجام می‌شود."/>
        <Button loading={busy} type="primary" htmlType="submit">ذخیره قوانین امتیاز</Button>
      </Form>
    </Card>
    <Card title="👥 پروفایل کاربران" size="small">
      <Table size="small" rowKey="user_id" dataSource={profiles} pagination={{pageSize:10}} scroll={{x:900}} columns={[
        {title:'شناسه عمومی',dataIndex:'code'},{title:'نام نمایشی',dataIndex:'display_name'},
        {title:'شناسه تلگرام (فقط مدیر)',dataIndex:'user_id'},
        {title:'شماره تماس (فقط مدیر)',dataIndex:'phone'},
        {title:'سن',dataIndex:'age'},{title:'استان',dataIndex:'province'},
        {title:'موقعیت (فقط مدیر)',render:(_:any,r:Row)=>r.location_lat!=null
          ?String(r.location_lat)+', '+String(r.location_lon):'—'},
        {title:'جنسیت',dataIndex:'gender'},{title:'نمایش',dataIndex:'visibility'},
        {title:'امتیاز',dataIndex:'points_balance',
          render:(v:number)=>(v??0).toLocaleString('fa-IR')},
        {title:'کیف پول (تومان)',dataIndex:'wallet_balance',
          render:(v:number)=>(v??0).toLocaleString('fa-IR')},
        {title:'مدیریت حساب',fixed:'right' as const,
          render:(_:any,r:Row)=><Button type="primary" onClick={()=>void openAccount(r)}>👤 پروفایل / عملیات مالی</Button>}
      ]}/>
    </Card>
    <Card title="💬 آرشیو گفتگوهای دوستانه و ناشناس" size="small">
      <Table size="small" rowKey="id" dataSource={chats} pagination={{pageSize:10}} scroll={{x:900}} columns={[
        {title:'گفتگو',dataIndex:'id'},
        {title:'طرف اول',render:(_:any,r:Row)=>(r.first_name||'کاربر')+' · '+r.first_code},
        {title:'طرف دوم',render:(_:any,r:Row)=>(r.second_name||'کاربر')+' · '+r.second_code},
        {title:'نوع',render:(_:any,r:Row)=>r.type==='friend'?'دوستانه':r.type==='filtered'?'فیلترشده':'تصادفی'},
        {title:'شروع',render:(_:any,r:Row)=>fmt(r.created)},
        {title:'پایان',render:(_:any,r:Row)=>r.closed?fmt(r.closed):'فعال'},
        {title:'پیام‌ها',dataIndex:'messages'},
        {title:'عملیات',render:(_:any,r:Row)=><Button onClick={()=>void inspect(r)}>مشاهده گفتگو</Button>}
      ]}/>
    </Card>
    <Card title="🚩 گزارش‌های کاربران" size="small">
      <Table size="small" rowKey="id" dataSource={reports} pagination={{pageSize:10}} scroll={{x:900}} columns={[
        {title:'شماره',dataIndex:'id'},{title:'گزارش‌دهنده',dataIndex:'reporter_code'},
        {title:'گزارش‌شده',dataIndex:'target_code'},{title:'دلیل',dataIndex:'reason'},
        {title:'وضعیت',dataIndex:'status'},
        {title:'عملیات',render:(_:any,r:Row)=>r.status==='pending'?<Space>
          <Button size="small" onClick={()=>void review(r.id,'resolved')}>رسیدگی شد</Button>
          <Button size="small" danger onClick={()=>void review(r.id,'block')}>مسدودسازی کاربر</Button>
        </Space>:<Tag>بررسی‌شده</Tag>}
      ]}/>
    </Card>
    <Modal open={!!editingUser}
      title={'👤 پروفایل و مدیریت حساب • '+(editingUser?.display_name||editingUser?.name||'کاربر')}
      onCancel={()=>{if(!accountBusy)setEditingUser(null);}}
      footer={<Button onClick={()=>setEditingUser(null)} disabled={accountBusy}>بستن</Button>}
      width={790} destroyOnHidden>
      {editingUser&&<Space direction="vertical" size="middle" style={{width:'100%'}}>
        <Space wrap>
          <Tag>شناسه عمومی: {editingUser.code}</Tag>
          <Tag>استان: {editingUser.province||'ثبت نشده'}</Tag>
          <Tag>سن: {editingUser.age||'ثبت نشده'}</Tag>
        </Space>
        <Space size="large" wrap>
          <Typography.Text strong>⭐ امتیاز: {Number(editingUser.points_balance||0).toLocaleString('fa-IR')}</Typography.Text>
          <Typography.Text strong>💰 کیف پول: {Number(editingUser.wallet_balance||0).toLocaleString('fa-IR')} تومان</Typography.Text>
        </Space>
        <Typography.Text strong>عملیات مدیر روی این کاربر</Typography.Text>
        <Space wrap>
          {Object.entries(adjustLabels).map(([op,label])=><Button key={op}
            type={editOperation===op?'primary':'default'} danger={op.endsWith('_sub')}
            onClick={()=>{setEditOperation(op);setEditAmount(null);setEditNote('');}}>{label}</Button>)}
        </Space>
        {editOperation&&<>
          <Alert type="info" showIcon
            title={adjustLabels[editOperation]}
            description="مقدار را مشخص کنید و پس از مشاهده مانده جدید، عملیات را تأیید کنید. نتیجه با اعلان تلگرام به کاربر اطلاع داده خواهد شد."/>
          <Space direction="vertical" style={{width:'100%'}}>
            <Typography.Text>مقدار ({editOperation.startsWith('points_')?'امتیاز':'تومان'})</Typography.Text>
            <InputNumber value={editAmount} onChange={setEditAmount} min={1}
              max={adjustLimits[editOperation]} precision={0} style={{width:'100%'}}/>
            <Typography.Text>توضیح برای کاربر (اختیاری)</Typography.Text>
            <Input.TextArea rows={2} maxLength={200} showCount value={editNote} onChange={e=>setEditNote(e.target.value)}
              placeholder="مثلاً پاداش فعالیت، اصلاح موجودی یا هدیه مدیریت"/>
          </Space>
          <Button loading={accountBusy} type="primary" danger={editOperation.endsWith('_sub')}
            onClick={confirmAdjustment}>بررسی مانده و تأیید عملیات</Button>
        </>}
        <Typography.Text strong>تاریخچه تغییرات مدیریتی</Typography.Text>
        <Table size="small" rowKey="request_id" dataSource={accountHistory}
          pagination={{pageSize:5}} scroll={{x:500}} columns={[
            {title:'عملیات',render:(_:any,r:Row)=>adjustLabels[r.operation]||r.operation},
            {title:'مقدار',render:(_:any,r:Row)=>Number(r.amount).toLocaleString('fa-IR')},
            {title:'موجودی جدید',render:(_:any,r:Row)=>Number(r.after_balance).toLocaleString('fa-IR')},
            {title:'توضیح',dataIndex:'note'},
            {title:'تاریخ',render:(_:any,r:Row)=>fmt(r.created)}
          ]}/>
      </Space>}
    </Modal>
    <Modal open={!!selected} title={'آرشیو گفتگو '+(selected?.id??'')}
      onCancel={()=>setSelected(null)} footer={<Button onClick={()=>setSelected(null)}>بستن</Button>}
      width={780}>
      <Alert type="info" showIcon title="اطلاعات مدیریت — محرمانه" style={{marginBottom:12}}
        description="تاریخچه صرفاً برای مدیر مجاز قابل مشاهده است. شناسه‌های داخلی نباید در صفحه کاربران منتشر شوند."/>
      <div style={{maxHeight:450,overflow:'auto'}}>
        {messages.length===0?<Typography.Text type="secondary">پیامی ثبت نشده است.</Typography.Text>:
        messages.map(r=><div key={r.id} style={{borderBottom:'1px solid #ddd',padding:'10px 0'}}>
          <Typography.Text strong>{r.display_name||r.public_code||'کاربر'}</Typography.Text>
          <Typography.Text type="secondary"> · {fmt(r.created)}</Typography.Text>
          <div style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{r.body}</div>
        </div>)}
      </div>
    </Modal>
  </Space>;
}
