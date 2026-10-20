import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import { App, ConfigProvider, Spin, Alert, Button } from 'antd';
import 'antd/dist/reset.css';
import './pages/vpn/SalesBotPage.css';
import './masiha-standalone.css';
import SalesBotPage from './pages/vpn/SalesBotPage';
import VPNPage from './pages/vpn/VPNPage';
import BackupRestorePanel from './pages/vpn/BackupRestorePanel';
import {setupHttp} from './api/http-init';

declare global {interface Window {X_UI_BASE_PATH: string;}}
const tabs = [
  {key:'bot', title:'تنظیمات ربات'},
  {key:'openvpn', title:'OpenVPN'},
  {key:'openconnect', title:'OpenConnect'},
  {key:'backup', title:'بکاپ و بازیابی'}
];
function Panel() {
  const [selected,setSelected]=useState((location.hash.slice(1)||'bot'));
  const nativePanel = (window as any).__masihaNativePanel || '/';
  useEffect(()=>{
    const changed=()=>setSelected(location.hash.slice(1)||'bot');
    addEventListener('hashchange', changed);
    return ()=>removeEventListener('hashchange',changed);
  },[]);
  return <div className="m-root" dir="rtl">
    <header className="m-header"><div><span className="m-eyebrow">MASIHA · VPN MANAGER</span><h1>مدیریت سرویس‌های اختصاصی</h1><p>ماژول مستقل از بروزرسانی‌های پنل اصلی سنایی</p></div>
      <a className="m-upstream" href={nativePanel+'panel/'}>بازگشت به پنل x-ui ←</a></header>
    <nav className="m-nav">{tabs.map(t=><a key={t.key} href={'#'+t.key} className={selected===t.key?'active':''}>{t.title}</a>)}</nav>
    <main className="m-content">{selected==='bot'?<SalesBotPage/>:
      selected==='openvpn'?<VPNPage protocol="openvpn"/>:
      selected==='openconnect'?<VPNPage protocol="openconnect"/>:
      selected==='backup'?<BackupRestorePanel/>:<Alert type="warning" message="صفحه یافت نشد"/>}</main>
    <footer className="m-footer">نسخه مستقل مسیحا — این بخش با آپدیت فایل اجرایی x-ui بازنویسی نمی‌شود.</footer>
  </div>;
}
async function boot(){
  // Path-scoped x-ui cookies are sent only to native routes. The injected
  // launcher stores the visited path (not credentials) in sessionStorage.
  // A direct bookmark on the default installation can use /xui/.
  const saved=window.sessionStorage.getItem('masiha:native-panel')||'';
  const candidate=(/^\/(?:[A-Za-z0-9._~-]+\/){1,6}$/.test(saved)&&!saved.includes('..'))
    ? saved : '/xui/';
  window.X_UI_BASE_PATH=candidate;
  const root=createRoot(document.getElementById('app')!);
  root.render(<div className="m-loading"><Spin size="large"/>در حال تأیید ورود مدیر…</div>);
  try {
    const r=await fetch(candidate+'panel/api/masiha-session', {
      credentials:'same-origin', headers:{'X-Requested-With':'XMLHttpRequest'},
      cache:'no-store'
    });
    if (!r.ok) throw new Error('نشست مدیر یا مسیر پنل معتبر نیست. از منوی «مدیریت مسیحا» در داخل پنل سنایی وارد شوید.');
    const data=await r.json();
    if (!data.success || typeof data.obj?.csrf_token!=='string' || typeof data.obj?.native_panel!=='string')
      throw new Error('نشست مدیریت معتبر نیست.');
    if(data.obj.native_panel!==candidate) throw new Error('مسیر پنل سنایی تغییر کرده است. مدیریت مسیحا را از منوی پنل سنایی باز کنید.');
    (window as any).__masihaNativePanel=data.obj.native_panel;
    window.X_UI_BASE_PATH=data.obj.native_panel;
    const meta=document.createElement('meta');meta.name='csrf-token';meta.content=data.obj.csrf_token;
    document.head.appendChild(meta);
    setupHttp();
    root.render(<ConfigProvider direction="rtl"><App><Panel/></App></ConfigProvider>);
  }catch(e) {
    root.render(<div className="m-auth"><h2>احراز هویت مدیر</h2><p>{String(e)}</p><Button type="primary" onClick={()=>window.location.reload()}>بررسی مجدد نشست مدیر</Button></div>);
  }
}
void boot();
