from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
s=s.replace("type Protocol = 'openvpn' | 'openconnect';","type Protocol = 'openvpn' | 'openconnect' | 'l2tp' | 'ikev2';",1)
s=s.replace("const title=protocol==='openvpn'?'OpenVPN':'OpenConnect';",
 "const title=protocol==='openvpn'?'OpenVPN':protocol==='openconnect'?'OpenConnect':protocol==='l2tp'?'L2TP/IPsec':'IKEv2/IPsec';",1)
s=s.replace("protocol==='openconnect'&&<Form.Item","protocol!=='openvpn'&&<Form.Item")
s=s.replace("<Button type=\"primary\" onClick={()=>edit(null)}>ساخت کاربر</Button>",
 "<Button type=\"primary\" disabled={['ikev2','l2tp'].includes(protocol)&&!data?.status.configured} onClick={()=>edit(null)}>ساخت کاربر</Button>",1)
s=s.replace("type Data = { users: User[]; status: { service: string; endpoint: string; online: Record<string, unknown>[] } };",
"type Data = { users: User[]; status: { service: string; endpoint: string; online: Record<string, unknown>[]; configured?:boolean } };",1)
s=s.replace("title={protocol==='openvpn'?'اتصال با فایل اختصاصی .ovpn':'اتصال با برنامه OpenConnect یا AnyConnect'}",
 "title={protocol==='openvpn'?'اتصال با فایل اختصاصی .ovpn':protocol==='openconnect'?'اتصال با OpenConnect':'اتصال '+title}")
s=s.replace("کاربران این دو سرویس در همین بخش مدیریت می‌شوند. حجم و سابسکریپشن Xray با این حساب‌ها مشترک نیست. انقضا حداکثر ظرف ۱۵ ثانیه اعمال می‌شود.",
 "حساب‌های مستقل این بخش حجم مشترک ندارند. برای ساخت سرویس دارای حجم و زمان مشترک، تب اشتراک مشترک را انتخاب کنید.")
s=s.replace(
 '   {error&&<Alert type="error" showIcon title={error} action={<Button onClick={()=>void reload()}>تلاش مجدد</Button>}/>}',
 '   {error&&<Alert type="error" showIcon title={error} action={<Button onClick={()=>void reload()}>تلاش مجدد</Button>}/>}\n   {["ikev2","l2tp"].includes(protocol)&&!data?.status.configured&&<Alert showIcon type="warning" title="تست سهمیه مشترک IPsec تکمیل نشده؛ ساخت حساب فعلاً غیرفعال است."/>}'
)
s=s.replace("disabled={busy||data?.status.service==='active'}",
  "disabled={busy||(['ikev2','l2tp'].includes(protocol)&&!data?.status.configured)||data?.status.service==='active'}")
s=s.replace("<Button disabled={busy}>راه‌اندازی مجدد</Button>",
  "<Button disabled={busy||(['ikev2','l2tp'].includes(protocol)&&!data?.status.configured)}>راه‌اندازی مجدد</Button>")
if "type Protocol = 'openvpn' | 'openconnect';" in s:raise RuntimeError("Protocol patch failed")
p.write_text(s)
print("PATCH_FRONT",s.count("shared"),len(s))