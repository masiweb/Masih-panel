import {useEffect,useState} from 'react';
import {Alert,App,Button,Card,Form,Space,Switch,Tag,Typography} from 'antd';
import {HttpUtil} from '@/utils';

type MediaSwitches = {
  broadcast_music: boolean;
  broadcast_movie: boolean;
  broadcast_series: boolean;
};
type MediaStats = MediaSwitches & {
  total_music?:number;
  total_films?:number;
};
const labels: Record<keyof MediaSwitches,string> = {
  broadcast_music:'🎵 اعلام و ارسال موزیک جدید به همه کاربران',
  broadcast_movie:'🎬 اعلام و ارسال فیلم سینمایی جدید',
  broadcast_series:'📺 اعلام و ارسال قسمت جدید سریال'
};

export default function MediaBroadcastSettings() {
  const {message}=App.useApp();
  const [form]=Form.useForm<MediaSwitches>();
  const [loaded,setLoaded]=useState<MediaStats>();
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const call=async(action:string,values?:Record<string,unknown>)=>{
    const r=await HttpUtil.post('/panel/api/sales-bot',
      {action,...values},{headers:{'Content-Type':'application/json'},silent:true,timeout:65000});
    if (!r?.success)throw new Error(r?.msg||'خطای ذخیره تنظیمات');
    return r.obj as any;
  };
  const refresh=async()=>{
    try{
      const d=await call('get');
      const m=d.media as MediaStats;
      setLoaded(m);
      form.setFieldsValue({
        broadcast_music:Boolean(m.broadcast_music),
        broadcast_movie:Boolean(m.broadcast_movie),
        broadcast_series:Boolean(m.broadcast_series)
      });
      setError('');
    }catch(e){setError(String(e));}
  };
  useEffect(()=>{void refresh();},[]);
  const save=async(v:MediaSwitches)=>{
    setBusy(true);
    try{
      await call('media_settings',{values:{
        broadcast_music:Boolean(v.broadcast_music),
        broadcast_movie:Boolean(v.broadcast_movie),
        broadcast_series:Boolean(v.broadcast_series)
      }});
      message.success('اعلان انتشار رسانه ذخیره شد');
      await refresh();
    }catch(e){message.error(String(e));}
    finally{setBusy(false);}
  };
  return <Card title="📣 اعلان خودکار انتشار موزیک، فیلم و سریال" size="small">
    <Space direction="vertical" size="middle" style={{width:'100%'}}>
      <Alert type="info" showIcon
        title="فایل مستقیماً برای کاربران ربات ارسال می‌شود"
        description="پس از انتشار هر رسانه جدید، همان فایل ذخیره‌شده در تلگرام همراه نام و دکمه پخش به همه کاربران فعال ارسال می‌شود؛ نه لینک دانلود. هر دسته کلید جدا دارد."/>
      <Alert type="warning" showIcon
        title="در واردسازی آرشیو حجیم دقت کنید"
        description="اگر اعلان موزیک فعال باشد، برای هر آهنگ تازه واردشده به تمام کاربران فعال ربات پیام جداگانه ارسال می‌شود. پیش‌فرض خاموش است. خاموش‌کردن هر دسته، اعلان‌های منتظر ارسال همان دسته را لغو می‌کند؛ اعلان‌های قبلاً ارسال‌شده قابل بازگشت نیستند."/>
      {error&&<Alert type="error" title={error}/>}
      <Form form={form} layout="vertical" onFinish={v=>void save(v)}>
        {Object.entries(labels).map(([key,label])=>
          <Form.Item key={key} name={key} label={label} valuePropName="checked">
            <Switch checkedChildren="فعال" unCheckedChildren="غیرفعال"/>
          </Form.Item>
        )}
        <Space wrap>
          <Button type="primary" htmlType="submit" loading={busy}>ذخیره تنظیمات اعلان رسانه</Button>
          <Button onClick={()=>void refresh()} disabled={busy}>بازخوانی وضعیت</Button>
          <Tag>موزیک: {loaded?.total_music??'—'}</Tag>
          <Tag>فیلم و سریال: {loaded?.total_films??'—'}</Tag>
        </Space>
      </Form>
      <Typography.Text type="secondary">
        تنظیمات فقط روی رسانه‌هایی که از این پس منتشر می‌شوند اثر دارد؛ آثار قدیمی به‌صورت خودکار دوباره برای کاربران ارسال نمی‌شوند.
      </Typography.Text>
    </Space>
  </Card>;
}
