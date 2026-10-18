(() => {
  if (!location.pathname.startsWith('/xui/panel')) return;
  if (document.getElementById('masiha-nav-widget')) return;
  const host=document.createElement('div');
  host.id='masiha-nav-widget';
  host.style.cssText='position:fixed;bottom:18px;right:18px;z-index:99999;direction:rtl;font-family:Tahoma,Arial,sans-serif';
  const toggle=document.createElement('button');
  toggle.textContent='⚙ مدیریت مسیحا';
  toggle.type='button';
  toggle.style.cssText='background:#1e40af;color:#fff;border:none;padding:13px 18px;border-radius:14px;box-shadow:0 7px 25px #0004;cursor:pointer;font-size:13px;font-weight:bold';
  const menu=document.createElement('div');
  menu.style.cssText='background:#fff;display:none;position:absolute;bottom:54px;right:0;min-width:225px;border:1px solid #d7e4f1;border-radius:12px;padding:8px;box-shadow:0 10px 28px #0003';
  const links=[['تنظیمات ربات تلگرام','#bot'],['OpenVPN','#openvpn'],['OpenConnect','#openconnect'],['پشتیبان‌گیری و بازیابی','#backup']];
  for(const [label,hash] of links){
    const a=document.createElement('a');
    a.href='/masiha-ui/'+hash;a.textContent=label;
    a.style.cssText='display:block;padding:10px 14px;margin:2px;color:#1e3558;font-weight:600;border-radius:8px;text-decoration:none';
    a.onmouseenter=()=>a.style.background='#e9f1ff';
    a.onmouseleave=()=>a.style.background='transparent';
    menu.appendChild(a);
  }
  toggle.onclick=()=>menu.style.display=menu.style.display==='none'?'block':'none';
  host.append(menu,toggle);
  document.body.appendChild(host);
})();
