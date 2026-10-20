from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text(encoding="utf-8")
imp="import BotTextsAdminPage from './BotTextsAdminPage';"
if imp not in s:
 anchor="import AiSalesAdminPage from './AiSalesAdminPage';"
 if s.count(anchor)!=1:raise SystemExit("Unsupported imports")
 s=s.replace(anchor,anchor+"\n"+imp,1)
if "<BotTextsAdminPage/>" not in s:
 anchor="{key:'templates',label:'متن‌های پیش‌فرض',children:<Card title="
 if s.count(anchor)!=1:raise SystemExit("Text tab anchor missing")
 s=s.replace(anchor,"{key:'templates',label:'متن‌های پیش‌فرض',children:<Space direction=\"vertical\" style={{width:'100%'}} size=\"large\"><BotTextsAdminPage/><Card title=",1)
 end=" </Card>},\n {key:'plans',"
 if s.count(end)!=1:raise SystemExit("Text tab closing anchor missing")
 s=s.replace(end," </Card></Space>},\n {key:'plans',",1)
p.write_text(s,encoding="utf-8")