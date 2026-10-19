#!/usr/bin/env python3
"""Idempotently mount the club user dossier inside SalesBotPage's Users tab."""
from pathlib import Path
import re,sys
p=Path(sys.argv[1]);s=p.read_text()
component_import="import ClubUserProfileModal from './ClubUserProfileModal';"
if component_import not in s:
 anchor="import SocialAdminPage from './SocialAdminPage';"
 if s.count(anchor)!=1:raise SystemExit("Cannot insert ClubUserProfileModal import")
 s=s.replace(anchor,anchor+"\n"+component_import,1)
if "const [clubUserId,setClubUserId]=useState<number|null>(null);" not in s:
 anchor=" const [planOpen,setPlanOpen]=useState(false);"
 if s.count(anchor)!=1:raise SystemExit("Cannot create user management dialog state")
 s=s.replace(anchor," const [clubUserId,setClubUserId]=useState<number|null>(null);\n"+anchor,1)
if "کلاب چت: پروفایل و مدیریت" not in s:
 old=re.search(r"(?m)^ \{key:'users',label:'کاربران و نمایندگان',children:table\(data\?\.users\|\|\[\],\[.*\]\)\},$",s)
 if not old:raise SystemExit("Unsupported Users & Resellers tab version")
 new=""" {key:'users',label:'کاربران و نمایندگان',children:<Space direction="vertical" style={{width:'100%'}}>
  <Alert type="info" showIcon description="روی نام یا دکمه پرونده کامل کاربر بزنید تا وضعیت پروفایل کلاب ناشناس، افتخارات، آثار، گفتگوها، اشتراک‌ها، امتیاز، کیف پول و تمام ابزارهای مدیریت در یک صفحه نمایش داده شود."/>
  {table(data?.users||[],[
    {title:'شناسه تلگرام (فقط مدیر)',dataIndex:'id'},
    {title:'کاربر',render:(_:unknown,r:Row)=><Button type="link" onClick={()=>setClubUserId(r.id)}>{r.name||'کاربر'}</Button>},
    {title:'کیف پول (تومان)',render:(_:unknown,r:Row)=>Number(r.balance||0).toLocaleString('fa-IR')},
    {title:'شماره',dataIndex:'phone'},
    {title:'مسدود',render:(_:unknown,r:Row)=><Switch checked={!!r.blocked} onChange={v=>void run('user',{id:r.id,blocked:v,reseller:!!r.reseller})}/>},
    {title:'نماینده',render:(_:unknown,r:Row)=><Switch checked={!!r.reseller} onChange={v=>void run('user',{id:r.id,blocked:!!r.blocked,reseller:v})}/>},
    {title:'عملیات',render:(_:unknown,r:Row)=><Space wrap>
      <Button type="primary" onClick={()=>setClubUserId(r.id)}>👤 کلاب چت: پروفایل و مدیریت</Button>
      <Button onClick={()=>{setEntry('');setDialog({action:'credit',id:r.id,title:'افزایش یا کاهش موجودی به تومان (عدد منفی: کاهش)',reference:crypto.randomUUID()})}}>اصلاح موجودی</Button>
    </Space>}
  ])}
 </Space>},"""
 s=s[:old.start()]+new+s[old.end():]
if "<ClubUserProfileModal " not in s:
 anchor=' <Modal width={740} open={planOpen}'
 if s.count(anchor)!=1:raise SystemExit("Cannot add integrated user modal")
 snippet=""" <ClubUserProfileModal userId={clubUserId} onClose={()=>setClubUserId(null)}
   onChanged={load} adminIds={data?.settings?.admins||[]}/>
"""
 s=s.replace(anchor,snippet+anchor,1)
p.write_text(s)
