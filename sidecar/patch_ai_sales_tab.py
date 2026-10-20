from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
imp="import AiSalesAdminPage from './AiSalesAdminPage';"
if imp not in s:
 anchor="import SocialAdminPage from './SocialAdminPage';"
 assert s.count(anchor)==1
 s=s.replace(anchor,anchor+'\n'+imp,1)
if '<AiSalesAdminPage/>' not in s:
 anchor="{key:'social_manager',label:'🌐 مدیریت کلاب ناشناس',children:<SocialAdminPage/>},"
 assert s.count(anchor)==1
 s=s.replace(anchor,anchor+"\n {key:'ai_sales',label:'🤖 فروش هوش مصنوعی',children:<AiSalesAdminPage/>},",1)
p.write_text(s)
