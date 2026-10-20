import {useEffect,useState} from 'react';
import {Alert,App,Button,Card,Form,Input,InputNumber,Modal,Select,Space,Switch,Table,Tag,Typography} from 'antd';
import {HttpUtil} from '@/utils';
type R=Record<string,any>;
const kindLabels:Record<string,string>={bot:'🤖 داخل ربات',personal:'📧 ایمیل شخصی',ready:'📨 ایمیل آماده'};
const operationLabels:Record<string,string>={text:'متن',image:'تصویر',video:'ویدیو'};
const providerLabels:Record<string,string>={openai:'OpenAI',google:'Google Gemini',groq:'Groq',cloudflare:'Cloudflare Workers AI',fal:'fal.ai'};
export default function AiSalesAdminPage(){
 const {message,modal}=App.useApp();
 const [state,setState]=useState<R>({models:[],plans:[],orders:[],stock:[]});
 const [loading,setLoading]=useState(false);
 const [providerForm]=Form.useForm();
 const [providerModels,setProviderModels]=useState<R[]>([]);
 const [providerStates,setProviderStates]=useState<R>({});
 const [priceForm]=Form.useForm();
 const [modelForm]=Form.useForm();
 const [planForm]=Form.useForm();
 const [stockForm]=Form.useForm();
 const [modelOpen,setModelOpen]=useState(false);
 const [planOpen,setPlanOpen]=useState(false);
 const [stockOpen,setStockOpen]=useState(false);
 const [selectedKind,setSelectedKind]=useState('bot');
 const call=async(action:string,values:R={})=>{
  const r=await HttpUtil.post('/panel/api/sales-bot',{action,...values},
    {headers:{'Content-Type':'application/json'},silent:true,timeout:65000});
  if(!r?.success)throw new Error(r?.msg||'خطای مدیریت');
  return r.obj as any;
 };
 const load=async()=>{
  try{
   const [d,costs,registry]=await Promise.all([
    call('ai_sale_get'),call('ai_sale_costs'),call('ai_model_catalog')
   ]);
   setState(d||{models:[],plans:[],orders:[],stock:[]});
   priceForm.setFieldsValue(costs||{});
   setProviderModels(registry?.models||[]);
   setProviderStates(registry?.providers||{});
  }
  catch(e){message.error(String(e));}
 };
 useEffect(()=>{void load()},[]);
 const save=async(action:string,values:R,close:()=>void)=>{
  setLoading(true);
  try{await call(action,{values});close();await load();message.success('تغییرات ذخیره شد');}
  catch(e){message.error(String(e));}
  finally{setLoading(false);}
 };
 const decide=(row:R,decision:string)=>{
  const label=decision==='reject'?'رد رسید':decision==='complete'?'اعلام اتمام فعال‌سازی':'تأیید رسید';
  let reason='';
  modal.confirm({title:label+' سفارش '+row.id,
   content:decision==='reject'?<Input.TextArea placeholder="علت عدم تأیید رسید (اجباری)" onChange={e=>{reason=e.target.value}}/>:
    <Typography.Text>پس از تأیید، اعتبار داخل ربات فعال می‌شود، کد ایمیل آماده تحویل می‌گردد، یا درخواست ایمیل شخصی در صف فعال‌سازی قرار می‌گیرد.</Typography.Text>,
   okText:'ثبت تصمیم',cancelText:'انصراف',
   okButtonProps:{danger:decision==='reject'},
   onOk:async()=>{
    if(decision==='reject'&&reason.trim().length<4){message.error('علت رد را وارد کنید');throw new Error('علت رد اجباری است');}
    await call('ai_sale_decide',{id:row.id,decision,reason});
    await load();message.success('تصمیم ثبت و پیام کاربر در صف ارسال قرار گرفت');
   }
  });
 };
 return <Space direction="vertical" style={{width:'100%'}} size="middle">
  <Alert type="info" showIcon title="🤖 فروش اشتراک‌های هوش مصنوعی"
    description="پلن‌های داخل ربات، ایمیل شخصی و ایمیل آماده مستقل از اشتراک VPN هستند. هزینه‌ها به تومان و سهمیه‌های داخل ربات به تعداد درخواست موفق محاسبه می‌شوند. هیچ رمز عبوری در ربات دریافت یا ذخیره نمی‌شود."/>
  <Card size="small" title="🌐 فهرست مدل‌های AI و کلید سرویس‌ها">
   <Alert type="warning" showIcon title="سهمیه رایگان، استفاده نامحدود یا بدون کلید نیست"
     description="مدل‌های علامت‌دار با سهمیه رایگان مشروط به حساب ارائه‌دهنده هستند. OpenAI اشتراک ChatGPT را به‌جای اعتبار API نمی‌پذیرد. ویدیوی OpenAI Sora دیگر در API فعال نیست؛ گزینه‌های fal به اعتبار پولی نیاز دارند." style={{marginBottom:12}}/>
   <Space wrap style={{marginBottom:12}}>
    {Object.entries(providerLabels).map(([key,label])=>
     <Tag key={key} color={providerStates[key]?'green':'default'}>
      {label} — {providerStates[key]?'کلید ثبت‌شده':'نیازمند کلید API'}
     </Tag>
    )}
   </Space>
   <Form form={providerForm} layout="vertical" onFinish={async v=>{
    const values=Object.fromEntries(Object.entries(v).filter(([_,x])=>typeof x==='string'&&String(x).trim()));
    if(!Object.keys(values).length){message.info('یک کلید یا شناسه حساب وارد کنید');return;}
    setLoading(true);
    try{await call('ai_provider_config',{values});providerForm.resetFields();await load();message.success('تنظیمات ارائه‌دهنده ذخیره شد');}
    catch(e){message.error(String(e));}finally{setLoading(false);}
   }}>
    <Space wrap>
     {Object.entries(providerLabels).map(([provider,label])=><Form.Item key={provider}
       name={'ai_provider_'+provider+'_key'} label={'کلید API — '+label}>
       <Input.Password placeholder="خالی = حفظ کلید قبلی" autoComplete="new-password" style={{minWidth:200}}/>
     </Form.Item>)}
     <Form.Item name="ai_provider_cloudflare_account" label="شناسه ۳۲ کاراکتری حساب Cloudflare">
       <Input placeholder="Cloudflare Account ID" dir="ltr" style={{minWidth:250}}/>
     </Form.Item>
    </Space>
    <Button type="primary" htmlType="submit" loading={loading}>ذخیره امن کلیدها</Button>
   </Form>
   <Table rowKey="model_key" size="small" dataSource={providerModels} pagination={{pageSize:10}} scroll={{x:690}}
     columns={[{title:'ارائه‌دهنده',render:(_:unknown,r:R)=>providerLabels[r.provider]||r.provider},
       {title:'مدل',dataIndex:'title'},
       {title:'نوع',render:(_:unknown,r:R)=>operationLabels[r.modality]||r.modality},
       {title:'هزینه',render:(_:unknown,r:R)=>r.tier==='free_tier'?<Tag color="blue">سهمیه رایگان محدود</Tag>:<Tag>پولی</Tag>},
       {title:'اتصال',render:(_:unknown,r:R)=>r.configured?<Tag color="green">کلید موجود</Tag>:<Tag color="orange">بدون کلید</Tag>},
       {title:'فعال در پلن‌ها',render:(_:unknown,r:R)=><Switch checked={!!r.active}
          onChange={async on=>{try{await call('ai_model_toggle',{model_key:r.model_key,active:on});await load();}
          catch(e){message.error(String(e));}}}/>}]}/>
  </Card>
  <Card size="small" title="⚙️ تعرفه‌های متن، عکس و ویدیو">
   <Form form={priceForm} layout="vertical" onFinish={async values=>{
     setLoading(true);
     try{await call('ai_sale_costs',{values});await load();message.success('تعرفه‌ها ذخیره شدند');}
     catch(e){message.error(String(e));}finally{setLoading(false);}
    }}>
    <Space wrap>
      <Form.Item name="ai_cost_text" label="هر پاسخ متنی"><InputNumber min={0} max={1000000}/></Form.Item>
      <Form.Item name="ai_cost_image" label="هر تصویر"><InputNumber min={0} max={1000000}/></Form.Item>
      <Form.Item name="ai_cost_video" label="هر ویدیو"><InputNumber min={0} max={1000000}/></Form.Item>
    </Space>
    <Form.Item name="ai_media_enabled" label="فعال‌سازی API تصویر و ویدیو" valuePropName="checked"><Switch/></Form.Item>
    <Space wrap>
      <Form.Item name="ai_image_model" label="مدل تصویر"><Input dir="ltr"/></Form.Item>
      <Form.Item name="ai_video_model" label="مدل ویدیو"><Input dir="ltr"/></Form.Item>
    </Space>
    <Alert type="info" showIcon title="API رسانه از کلید تنظیمات اصلی ربات استفاده می‌کند." description="مدل‌های تصویر و ویدیو باید در حساب OpenAI مجاز باشند؛ ویدیوی خروجی ۸ ثانیه است."/>
    <Button style={{marginTop:12}} htmlType="submit" type="primary" loading={loading}>ذخیره تعرفه و مدل</Button>
   </Form>
  </Card>
  <Card size="small" title="مدل‌ها و سرویس‌های قابل فروش"
    extra={<Button type="primary" onClick={()=>{modelForm.resetFields();setModelOpen(true)}}>افزودن مدل</Button>}>
   <Table rowKey="id" size="small" dataSource={state.models||[]} pagination={{pageSize:8}}
    columns={[{title:'نام مدل',dataIndex:'name'},{title:'وضعیت',render:(_:unknown,r:R)=>r.active?<Tag color="green">فعال</Tag>:<Tag>غیرفعال</Tag>},
     {title:'ویرایش',render:(_:unknown,r:R)=><Button onClick={()=>{modelForm.setFieldsValue(r);setModelOpen(true)}}>ویرایش</Button>}]}/>
  </Card>
  <Card size="small" title="پلن‌های فروش هوش مصنوعی"
    extra={<Button type="primary" onClick={()=>{planForm.resetFields();setSelectedKind('bot');setPlanOpen(true)}}>افزودن پلن</Button>}>
   <Table rowKey="id" size="small" dataSource={state.plans||[]} scroll={{x:780}} pagination={{pageSize:12}}
    columns={[{title:'مدل',dataIndex:'model_name'},{title:'نام پلن',dataIndex:'name'},
     {title:'نوع',render:(_:unknown,r:R)=>kindLabels[r.kind]||r.kind},
     {title:'قیمت (تومان)',render:(_:unknown,r:R)=>Number(r.price).toLocaleString('fa-IR')},
     {title:'مدت',render:(_:unknown,r:R)=>r.days+' روز'},
     {title:'سهمیه‌ها (متن/عکس/ویدیو)',render:(_:unknown,r:R)=>[r.credits_text,r.credits_image,r.credits_video].join(' / ')},
     {title:'مدل‌ها (متن / تصویر / ویدیو)',render:(_:unknown,r:R)=>['text','image','video'].map(op=>{
       const m=providerModels.find(x=>x.model_key===r['model_'+op]);
       return m?.title||'تعریف نشده';
      }).join(' / ')},
     {title:'آماده تحویل',render:(_:unknown,r:R)=>r.kind==='ready'?r.stock:'—'},
     {title:'وضعیت',render:(_:unknown,r:R)=>r.active?'فعال':'غیرفعال'},
     {title:'مدیریت',render:(_:unknown,r:R)=><Button onClick={()=>{planForm.setFieldsValue(r);setSelectedKind(r.kind);setPlanOpen(true)}}>ویرایش</Button>}]}/>
  </Card>
  <Card size="small" title="موجودی کدهای فعال‌سازی ایمیل آماده"
    extra={<Button onClick={()=>{stockForm.resetFields();setStockOpen(true)}}>افزودن کد</Button>}>
   <Alert type="warning" showIcon title="فقط کدهای رسمی فعال‌سازی را ثبت کنید؛ نه ایمیل یا گذرواژه حساب‌ها."/>
   <Table size="small" rowKey="plan_id" dataSource={state.stock||[]}
    columns={[{title:'شناسه پلن',dataIndex:'plan_id'},{title:'کل کدها',dataIndex:'stock'},
     {title:'موجود برای تحویل',dataIndex:'available'}]}/>
  </Card>
  <Card size="small" title="سفارش‌ها و رسیدهای هوش مصنوعی">
   <Button style={{marginBottom:10}} onClick={()=>void load()}>بروزرسانی سفارش‌ها</Button>
   <Table rowKey="id" size="small" dataSource={state.orders||[]} scroll={{x:750}} pagination={{pageSize:15}}
    columns={[{title:'شماره سفارش',render:(_:unknown,r:R)=>r.id},
     {title:'شناسه تلگرام (مدیر)',dataIndex:'user_id'},{title:'شناسه پلن',dataIndex:'plan_id'},
     {title:'ایمیل شخصی',dataIndex:'email'},{title:'وضعیت',dataIndex:'status'},
     {title:'علت رد',dataIndex:'reason'},
     {title:'عملیات',render:(_:unknown,r:R)=><Space wrap>
      {r.status==='review'&&<>
       <Button type="primary" onClick={()=>decide(r,'approve')}>تأیید رسید</Button>
       <Button danger onClick={()=>decide(r,'reject')}>رد با علت</Button>
      </>}
      {r.status==='activating'&&<Button type="primary" onClick={()=>decide(r,'complete')}>اعلام فعال‌سازی</Button>}
     </Space>}]}/>
  </Card>
  <Modal open={modelOpen} onCancel={()=>setModelOpen(false)} title="افزودن/ویرایش مدل" footer={null}>
   <Form form={modelForm} layout="vertical" onFinish={v=>void save('ai_sale_model',v,()=>setModelOpen(false))}>
    <Form.Item name="id" hidden><Input/></Form.Item>
    <Form.Item name="name" label="نام مدل یا سرویس" rules={[{required:true}]}><Input placeholder="مثلاً مدل متصل به API"/></Form.Item>
    <Form.Item name="active" label="فعال" valuePropName="checked" initialValue={true}><Switch/></Form.Item>
    <Button htmlType="submit" type="primary" loading={loading}>ذخیره</Button>
   </Form>
  </Modal>
  <Modal open={planOpen} onCancel={()=>setPlanOpen(false)} title="پلن اشتراک هوش مصنوعی" footer={null} width={660}>
   <Form form={planForm} layout="vertical" onFinish={v=>void save('ai_sale_plan',v,()=>setPlanOpen(false))}>
    <Form.Item name="id" hidden><Input/></Form.Item>
    <Form.Item name="model_id" label="مدل" rules={[{required:true}]}>
      <Select options={(state.models||[]).map((r:R)=>({value:r.id,label:r.name}))}/>
    </Form.Item>
    <Form.Item name="kind" label="نوع فروش" rules={[{required:true}]} initialValue="bot">
      <Select onChange={setSelectedKind} options={Object.entries(kindLabels).map(([value,label])=>({value,label}))}/>
    </Form.Item>
    <Form.Item name="name" label="نام پلن" rules={[{required:true}]}><Input/></Form.Item>
    <Space wrap>
      <Form.Item name="price" label="قیمت (تومان)" rules={[{required:true}]}><InputNumber min={0} max={1000000000}/></Form.Item>
      <Form.Item name="days" label="مدت (روز)" rules={[{required:true}]}><InputNumber min={1} max={730}/></Form.Item>
    </Space>
    {selectedKind==='bot'&&<Space direction="vertical" style={{width:'100%'}}>
     {Object.entries(operationLabels).map(([op,label])=><Space wrap key={op}>
       <Form.Item name={'credits_'+op} label={'سهمیه '+label} initialValue={0}>
        <InputNumber min={0} max={1000000}/>
       </Form.Item>
       <Form.Item name={'model_'+op} label={'مدل '+label+' (مستقل از سایر بخش‌ها)'}>
        <Select allowClear showSearch optionFilterProp="label" style={{minWidth:280}} placeholder={'انتخاب مدل '+label}
          options={providerModels.filter(m=>m.modality===op&&m.active).map(m=>({
           value:m.model_key,label:(providerLabels[m.provider]||m.provider)+' / '+m.title+
            (m.tier==='free_tier'?' — سهمیه رایگان محدود':' — پولی')+(!m.configured?' ⚠️ کلید ندارد':'')
          }))}/>
       </Form.Item>
      </Space>)}
    </Space>}
    <Form.Item name="active" label="فعال" valuePropName="checked" initialValue={true}><Switch/></Form.Item>
    <Button htmlType="submit" type="primary" loading={loading}>ذخیره پلن</Button>
   </Form>
  </Modal>
  <Modal open={stockOpen} onCancel={()=>setStockOpen(false)} title="افزودن کد فعال‌سازی رسمی" footer={null}>
   <Form form={stockForm} layout="vertical" onFinish={v=>void save('ai_sale_stock',v,()=>setStockOpen(false))}>
    <Form.Item name="plan_id" label="پلن ایمیل آماده" rules={[{required:true}]}>
     <Select options={(state.plans||[]).filter((p:R)=>p.kind==='ready').map((p:R)=>({value:p.id,label:p.model_name+' / '+p.name}))}/>
    </Form.Item>
    <Form.Item name="delivery_code" label="کد رسمی فعال‌سازی یا ووچر" rules={[{required:true}]}>
      <Input.Password autoComplete="off" placeholder="فقط کد فعال‌سازی؛ بدون اطلاعات ورود"/>
    </Form.Item>
    <Button htmlType="submit" type="primary" loading={loading}>افزودن به موجودی</Button>
   </Form>
  </Modal>
 </Space>;
}
