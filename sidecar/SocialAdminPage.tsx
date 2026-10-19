import {useEffect,useState} from 'react';
import {Alert,App,Button,Card,Form,InputNumber,Modal,Space,Table,Tag,Typography} from 'antd';
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
export default function SocialAdminPage(){
  const {message}=App.useApp();
  const [stats,setStats]=useState<Row>({});
  const [profiles,setProfiles]=useState<Row[]>([]);
  const [chats,setChats]=useState<Row[]>([]);
  const [reports,setReports]=useState<Row[]>([]);
  const [messages,setMessages]=useState<Row[]>([]);
  const [selected,setSelected]=useState<Row|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [form]=Form.useForm();
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
        {title:'جنسیت',dataIndex:'gender'},{title:'نمایش',dataIndex:'visibility'}
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
