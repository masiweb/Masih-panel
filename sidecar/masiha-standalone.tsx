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
  useEffect(()=>{
    const changed=()=>setSelected(location.hash.slice(1)||'bot');
    addEventListener('hashchange', changed);
    return ()=>removeEventListener('hashchange',changed);
  },[]);
  return <div className="m-root" dir="rtl">
    <header className="m-header"><div><span className="m-eyebrow">MASIHA · VPN MANAGER</span><h1>مدیریت سرویس‌های اختصاصی</h1><p>ماژول مستقل از بروزرسانی‌های پنل اصلی سنایی</p></div>
      <a className="m-upstream" href="/xui/panel/">بازگشت به پنل x-ui ←</a></header>
    <nav className="m-nav">{tabs.map(t=><a key={t.key} href={'#'+t.key} className={selected===t.key?'active':''}>{t.title}</a>)}</nav>
    <main className="m-content">{selected==='bot'?<SalesBotPage/>:
      selected==='openvpn'?<VPNPage protocol="openvpn"/>:
      selected==='openconnect'?<VPNPage protocol="openconnect"/>:
      selected==='backup'?<BackupRestorePanel/>:<Alert type="warning" message="صفحه یافت نشد"/>}</main>
    <footer className="m-footer">نسخه مستقل مسیحا — این بخش با آپدیت فایل اجرایی x-ui بازنویسی نمی‌شود.</footer>
  </div>;
}
async function boot(){
  window.X_UI_BASE_PATH='/xui/';
  const root=createRoot(document.getElementById('app')!);
  root.render(<div className="m-loading"><Spin size="large"/>در حال تأیید ورود مدیر…</div>);
  try {
    const r=await fetch('/xui/panel/csrf-token', {credentials:'same-origin', headers:{'X-Requested-With':'XMLHttpRequest'}});
    if (!r.ok) throw new Error('ابتدا به پنل x-ui وارد شوید.');
    const data=await r.json();
    if (!data.success || typeof data.obj!=='string') throw new Error('نشست مدیریت معتبر نیست.');
    const meta=document.createElement('meta');meta.name='csrf-token';meta.content=data.obj;
    document.head.appendChild(meta);
    setupHttp();
    root.render(<ConfigProvider direction="rtl"><App><Panel/></App></ConfigProvider>);
  }catch(e) {
    root.render(<div className="m-auth"><h2>احراز هویت مدیر</h2><p>{String(e)}</p><Button type="primary" href="/xui/">ورود به پنل سنایی</Button></div>);
  }
}
void boot();
