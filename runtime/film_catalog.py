#!/usr/bin/python3
"""Film2Media catalogue scanner and bulk queue manager for Masiha VPN bot."""
import html
import http.client
import ipaddress
import json
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path('/opt/masiha-vpn-extension')
SITE='https://www.f2mc.top'
API=SITE+'/wp-json/wp/v2/'
UA='MasihaCatalogManager/1.0 (+licensed media library admin)'
VIDEO_RE=re.compile(r"https?://[^\"'<>\s]+?\.(?:mkv|mp4|m4v|mov|avi|webm|ts|m2ts)(?:\?[^\"'<>\s]*)?",re.I)
QUALITY_RE=re.compile(r"(?<!\d)(2160|1440|1080|720|480)p",re.I)
EP_RE=re.compile(r"(?i)S(\d{1,2})[ ._-]*E(\d{1,4})")
X_EP_RE=re.compile(r"(?i)(\d{1,2})x(\d{1,4})")
sys.path.insert(0,str(ROOT))
import film_importer as fi

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta=[];self.images=[];self.h1=[];self._h1=False;self.text=[]
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        if tag=='meta':self.meta.append(d)
        elif tag=='img':self.images.append(d)
        elif tag=='h1':self._h1=True
    def handle_endtag(self,tag):
        if tag=='h1':self._h1=False
    def handle_data(self,data):
        if data.strip():self.text.append(data.strip())
        if self._h1 and data.strip():self.h1.append(data.strip())

def fetch(url,limit=6_000_000,timeout=45):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/json,*/*'})
    last=None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=timeout) as response:
                raw=response.read(limit+1)
                if len(raw)>limit:raise ValueError('source_response_too_large')
                return raw,dict(response.headers)
        except http.client.IncompleteRead as ex:
            last=ex
        except Exception as ex:last=ex
        time.sleep(1.0+attempt)
    try:
        r=subprocess.run(['curl','-fsSL','--retry','2','--retry-all-errors','--max-time',str(timeout),
          '-A',UA,url],capture_output=True,timeout=timeout+8)
        if r.returncode==0 and len(r.stdout)<=limit:return r.stdout,{}
    except Exception:pass
    raise RuntimeError('source_fetch_'+type(last).__name__ if last else 'source_fetch_failed')

def text_only(value):
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',value or ''))).strip()

def meta_values(parser,key,attr='property'):
    return [x.get('content','').strip() for x in parser.meta
            if x.get(attr,'').lower()==key.lower() and x.get('content')]

def extract_label(source,label,allow_no_colon=False):
    plain=text_only(source) if '<' in source else re.sub(r'\s+',' ',source or '')
    stops=r'(?:کشور سازنده|زبان|ژانرها?|ژانر|بازیگران|انتشار|اکران|زمان|مدت|قسمت|فصل|تعداد قسمت|کیفیت|IMDb|اطلاعات فیلم|اطلاعات سریال|باکس دانلود|دانلود زیرنویس|نظرات|دوست داشتم|دوست نداشتم|پخش تریلر|ورود به حساب)'
    separator=r'\s*[:：]\s*' if not allow_no_colon else r'\s*[:：]?\s+'
    pattern=re.escape(label)+separator+r'(.+?)(?=\s+'+stops+r'\s*[:：]?|$)'
    matches=list(re.finditer(pattern,plain,re.I))
    return matches[-1].group(1).strip(' :：-')[:180] if matches else ''

def parse_episode(filename):
    m=EP_RE.search(filename)
    if m:return int(m.group(1)),int(m.group(2))
    m=X_EP_RE.search(filename)
    if m:return int(m.group(1)),int(m.group(2))
    m=re.search(r'(?i)season[ ._-]*(\d+).*?episode[ ._-]*(\d+)',filename)
    if m:return int(m.group(1)),int(m.group(2))
    return 0,0

def variants_from_html(source,media_type):
    normal=html.unescape(source).replace('\\/','/').replace('\\u0026','&').replace('\\x26','&')
    found=[];seen=set()
    for raw in VIDEO_RE.findall(normal):
        url=raw.rstrip(').,;]')
        try:parts=urllib.parse.urlsplit(url);name=urllib.parse.unquote(parts.path.rsplit('/',1)[-1])
        except Exception:continue
        if re.search(r'(?i)(trailer|teaser|sample|featurette|behind.the.scenes)',name):continue
        qm=QUALITY_RE.search(name)
        if not qm:continue
        season,episode=parse_episode(name)
        if media_type=='series' and not (season>0 and episode>0):continue
        if media_type=='movie':season=episode=0
        q=qm.group(1)+'p'
        stem=re.sub(r'\.(mkv|mp4|m4v|mov|avi|webm|ts|m2ts)$','',name,flags=re.I)
        tokens=[x for x in re.split(r'[ ._-]+',stem) if x]
        low=[x.lower() for x in tokens]
        source='WEB-DL' if 'web' in low and 'dl' in low else ('WEBRip' if 'webrip' in low else ('BluRay' if any(x in low for x in ('bluray','brrip','blu-ray')) else ('HDRip' if 'hdrip' in low else '')) )
        bit='10bit' if '10bit' in low else ('8bit' if '8bit' in low else '')
        codec='x265' if any(x in low for x in ('x265','h265','hevc')) else ('x264' if any(x in low for x in ('x264','h264','avc')) else '')
        encoder=next((x.upper() if x.lower() in ('psa','yify','pahe') else x for x in tokens if x.lower() in ('psa','yify','pahe','amzn','nf','dsnp')), '')
        version=' '.join(x for x in (source,bit,codec,encoder) if x)[:90]
        lower=(parts.path+' '+name).lower()
        dubbed='yes' if ('dubbed' in lower or '/dub/' in lower or '.dub.' in lower) else ('no' if '/sub/' in lower or 'farsi.sub' in lower else 'unknown')
        subtitles='hardsub' if 'hardsub' in lower else ('softsub' if '/sub/' in lower or 'farsi.sub' in lower else 'none' if dubbed=='yes' else 'unknown')
        language='دوبله فارسی' if dubbed=='yes' else ('زبان اصلی / زیرنویس فارسی' if subtitles!='unknown' else 'زبان اصلی')
        # Ignore alternate encodes/subtitle variants of the same quality.
        # A separate option is allowed only when the file is dubbed.
        key=(season,episode,q,"dubbed" if dubbed=="yes" else "original")
        if key in seen:continue
        seen.add(key)
        found.append({'season':season,'episode':episode,'quality':q,'version':version,
          'dubbed':dubbed,'subtitles':subtitles,'language':language,'url':url,'send_method':'sendVideo'})
    return found

def build_entry(post,media_type,source_html):
    parser=PageParser();parser.feed(source_html)
    raw_title=text_only((post.get('title') or {}).get('rendered',''))
    title=re.sub(r'^دانلود\s*(?:فیلم|سریال|انیمیشن)\s*','',raw_title)
    title=re.sub(r'\s*(?:بدون سانسور|با زیرنویس فارسی|با دوبله فارسی|دانلود رایگان).*$','',title,flags=re.I).strip(' -|')
    original=' '.join(dict.fromkeys(parser.h1))[:240]
    if not title:title=original or text_only(post.get('slug',''))
    if not original:original=title
    year_match=re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d)',original+' '+title+' '+post.get('slug',''))
    year=int(year_match.group(0)) if year_match else 0
    title=re.sub(r'\s+(?:19|20)\d{2}$','',title).strip()
    original=re.sub(r'\s+(?:19|20)\d{2}$','',original).strip()
    descs=meta_values(parser,'og:description')
    description=next((x for x in reversed(descs) if len(x)>55 and 'دانلود فیلم بدون سانسور' not in x), '')
    if not description:description=text_only((post.get('excerpt') or {}).get('rendered',''))
    description=description[:3500]
    images=[]
    for im in parser.images:
        u=im.get('data-src') or im.get('data-lazy-src') or im.get('src') or ''
        if not u or u.startswith('data:'):continue
        u=urllib.parse.urljoin(SITE,u)
        cls=im.get('class','').lower()
        if any(x in cls for x in ('mb-coverimg','coverimg','wp-post-image')):
            images.append((0 if 'mb-coverimg' in cls or 'coverimg' in cls else 1,u))
    cover=sorted(images)[0][1] if images else ''
    if not cover:
        og=meta_values(parser,'og:image')
        cover=next((u for u in og if re.search(r'\.(?:jpg|jpeg|png|webp)(?:\?|$)',u,re.I)), '')
    plain=' '.join(parser.text)
    country=extract_label(plain,'کشور سازنده')
    language=extract_label(plain,'زبان')
    genre=extract_label(plain,'ژانرها') or extract_label(plain,'ژانر')
    director=extract_label(plain,'کارگردان')
    certification=extract_label(plain,'رده سنی') or extract_label(plain,'محدودیت سنی')
    actors=extract_label(plain,'بازیگران',True)
    if 'دانلود زیرنویس' in actors:actors=''
    rm=re.search(r'(?<!\d)(10(?:\.0)?|[0-9](?:\.[0-9])?)\s*/\s*10',plain)
    rating=float(rm.group(1)) if rm else None
    slug=re.sub(r'[^A-Za-z0-9._:-]+','-',str(post.get('slug') or post.get('id') or 'film')).strip('-')
    if media_type=='series':slug='series:'+slug
    else:slug=slug[:160]
    source_page=post.get('link') or ''
    variants=variants_from_html(source_html,media_type)
    if language:
        for v in variants:
            if v['dubbed']!='yes':v['language']=(language[:25]+(' / Persian subtitles' if v['subtitles'] not in ('none','unknown') else ''))[:50]
    if not variants:return None
    if len(variants)>2000:variants=variants[:2000]
    return {'source':'film2media','rights_ok':False,'external_key':slug,'media_type':media_type,
      'title':title[:240],'original_title':original[:240],'genre':genre[:350],'year':year,
      'director':director[:250],'actors':actors[:600],'description':description,'rating':rating,
      'rating_source':'IMDb' if rating is not None else '', 'cover_url':cover,
      'country':country[:120],'certification':certification[:40],
      'source_page':source_page,'variants':variants}

def wp_page(kind,page):
    if kind=='movie':endpoint='posts';params={'categories':1}
    else:endpoint='series';params={}
    params.update({'per_page':100,'page':page,'_fields':'id,slug,link,title,excerpt,date,categories,featured_media'})
    url=API+endpoint+'?'+urllib.parse.urlencode(params)
    raw,headers=fetch(url,limit=8_000_000,timeout=40)
    data=json.loads(raw.decode('utf-8-sig'))
    if not isinstance(data,list):raise RuntimeError('wordpress_catalog_response_invalid')
    total=int(headers.get('X-WP-Total','0') or 0);pages=int(headers.get('X-WP-TotalPages','0') or 0)
    return data,total,pages

def update_run(run_id,**values):
    if not values:return
    with fi.connect() as c:
        sql=','.join(k+'=?' for k in values)
        c.execute('UPDATE film_scan_runs SET '+sql+' WHERE id=?',(*values.values(),run_id));c.commit()

def scan(run_id):
    with fi.connect() as c:
        run=c.execute('SELECT * FROM film_scan_runs WHERE id=?',(run_id,)).fetchone()
        if not run:raise ValueError('scan_run_not_found')
        scope=run['scope'];limit=int(run['item_limit'])
        c.execute("UPDATE film_scan_runs SET status='running',started=?,updated=?,last_error='' WHERE id=?",(fi.now(),fi.now(),run_id));c.commit()
    sources=[]
    if scope in ('all','movie'):sources.append('movie')
    if scope in ('all','series'):sources.append('series')
    total=processed=discovered=skipped=errors=0
    first={};totals={}
    for kind in sources:
        try:
            first[kind]=wp_page(kind,1);totals[kind]=first[kind][1]
        except Exception:
            totals[kind]=0;errors+=1
    total=sum(min(v,limit) if limit else v for v in totals.values())
    update_run(run_id,total=total,updated=fi.now(),errors=errors)
    for kind in sources:
        api_total=totals.get(kind,0)
        max_items=min(api_total,limit) if limit else api_total
        page_count=(max_items+99)//100
        for page in range(1,page_count+1):
            try:
                posts=first[kind][0] if page==1 and kind in first else wp_page(kind,page)[0]
            except Exception:
                errors+=1;update_run(run_id,errors=errors,updated=fi.now());continue
            for post in posts:
                if processed>=total:break
                if kind=='movie' and 1 not in (post.get('categories') or []):continue
                try:
                    page_url=post.get('link') or ''
                    host=(urllib.parse.urlsplit(page_url).hostname or '').lower()
                    if host not in ('www.f2mc.top','f2mc.top','www.myf2ms.top'):
                        raise ValueError('article_host_not_allowed')
                    raw,_=fetch(page_url,limit=6_000_000,timeout=45)
                    entry=build_entry(post,kind,raw.decode('utf-8','replace'))
                    if entry and entry['variants']:
                        with fi.connect() as c:
                            c.execute('''INSERT OR IGNORE INTO film_scan_items
                              (scan_id,external_key,title,media_type,variant_count,entry_json,state)
                              VALUES(?,?,?,?,?,?, 'discovered')''',
                              (run_id,entry['external_key'],entry['title'],entry['media_type'],
                               len(entry['variants']),json.dumps(entry,ensure_ascii=False)))
                            inserted=c.execute('SELECT changes()').fetchone()[0];c.commit()
                        if inserted:discovered+=1
                        else:skipped+=1
                    else:skipped+=1
                except Exception as ex:
                    errors+=1
                    with fi.connect() as c:
                        c.execute("INSERT OR IGNORE INTO film_scan_items(scan_id,external_key,title,media_type,variant_count,entry_json,state,error) VALUES(?,?,?,?,0,?,'error',?)",
                          (run_id,str(post.get('slug') or post.get('id')),text_only((post.get('title') or {}).get('rendered',''))[:240],kind,'{}',type(ex).__name__))
                        c.commit()
                processed+=1
                if processed%5==0 or processed==total:
                    update_run(run_id,processed=processed,discovered=discovered,skipped=skipped,errors=errors,updated=fi.now())
                time.sleep(0.12)
            if processed>=total:break
    update_run(run_id,status='done',total=total,processed=processed,discovered=discovered,
      skipped=skipped,errors=errors,updated=fi.now())

def ensure_hosts(entries):
    data=json.loads(fi.CONFIG.read_text(encoding='utf-8'));hosts=set(data.get('download_hosts',[]))
    candidates=set()
    for entry in entries:
        for key in ('source_page','cover_url'):
            h=urllib.parse.urlsplit(entry.get(key,'')).hostname
            if h:candidates.add(h.lower())
        for v in entry.get('variants',[]):
            h=urllib.parse.urlsplit(v.get('url','')).hostname
            if h:candidates.add(h.lower())
    patterns={}
    for h in candidates:
        pattern='*.abrtech.top' if h.endswith('.abrtech.top') else ('*.mehracdn.com' if h.endswith('.mehracdn.com') else h)
        patterns.setdefault(pattern,[]).append(h)
    for pattern,verify_hosts in sorted(patterns.items()):
        if pattern in hosts:continue
        for host in verify_hosts:
            try:
                infos=socket.getaddrinfo(host,443,proto=socket.IPPROTO_TCP)
                if not infos or any(not ipaddress.ip_address(x[4][0]).is_global for x in infos):continue
            except Exception:continue
            if len(hosts)<30:hosts.add(pattern)
            break
    data['download_hosts']=sorted(hosts)
    tmp=fi.CONFIG.with_name(fi.CONFIG.name+'.next')
    tmp.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
    tmp.chmod(0o600);tmp.replace(fi.CONFIG)

def add_all(run_id):
    with fi.connect() as c:
        run=c.execute('SELECT * FROM film_scan_runs WHERE id=?',(run_id,)).fetchone()
        if not run:raise ValueError('scan_run_not_found')
        if run['status']!='done':raise ValueError('scan_must_finish_before_bulk_add')
        if run['add_status']=='running':raise ValueError('bulk_add_already_running')
        rows=c.execute("SELECT entry_json FROM film_scan_items WHERE scan_id=? AND state='discovered' ORDER BY id",(run_id,)).fetchall()
        entries=[json.loads(r['entry_json']) for r in rows]
        c.execute("UPDATE film_scan_runs SET add_status='running',add_total=?,add_processed=0,add_variants=0,add_errors=0,updated=? WHERE id=?",(len(entries),fi.now(),run_id));c.commit()
    ensure_hosts(entries)
    config=fi.settings();processed=added=errors=0
    for entry in entries:
        try:
            entry['rights_ok']=True
            entry['silent_import']=True
            with fi.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                try:count=fi.stage(c,entry,config['hosts']);c.commit()
                except Exception:c.rollback();raise
            added+=count;state='queued' if count else 'already_added';err=''
        except Exception as ex:
            state='error';err=type(ex).__name__;errors+=1
        with fi.connect() as c:
            c.execute("UPDATE film_scan_items SET state=?,error=? WHERE scan_id=? AND external_key=?",(state,err,run_id,entry['external_key']))
            c.commit()
        processed+=1
        if processed%10==0 or processed==len(entries):
            update_run(run_id,add_processed=processed,add_variants=added,add_errors=errors,updated=fi.now())
    update_run(run_id,add_status='done',add_total=len(entries),add_processed=processed,
      add_variants=added,add_errors=errors,updated=fi.now())

if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1] not in ('scan','add-all'):
        raise SystemExit('usage: film_catalog.py scan|add-all RUN_ID')
    try:
        if sys.argv[1]=='scan':scan(sys.argv[2])
        else:add_all(sys.argv[2])
    except Exception as exc:
        with fi.connect() as c:
            if sys.argv[1]=='scan':
                c.execute("UPDATE film_scan_runs SET status='failed',last_error=?,updated=? WHERE id=?",(type(exc).__name__,fi.now(),sys.argv[2]))
            else:
                c.execute("UPDATE film_scan_runs SET add_status='failed',last_error=?,updated=? WHERE id=?",(type(exc).__name__,fi.now(),sys.argv[2]))
            c.commit()
        raise