#!/usr/bin/python3
"""Film2Media discovery with incremental staging into the Telegram upload queue."""
import argparse, json, sys, threading, time, uuid
import film_importer as fi
import film_catalog as catalog

BATCH=25

def reserve_run(scope,limit,resume_active):
    with fi.connect() as c:
        active=c.execute("""SELECT id,status FROM film_scan_runs
          WHERE status IN ('queued','running') ORDER BY started DESC LIMIT 1""").fetchone()
        if active:
            if not resume_active:
                print("PIPELINE_SKIPPED_ACTIVE_SCAN",active["id"],active["status"],flush=True)
                return None
            run_id=active["id"]
            print("PIPELINE_RESUMING_SCAN",run_id,active["status"],flush=True)
            c.execute("""UPDATE film_scan_items SET state='discovered'
              WHERE scan_id=? AND state='staging'""",(run_id,))
            c.execute("""UPDATE film_scan_runs SET scope=?,item_limit=?,status='queued',
              add_status='running',last_error='',updated=? WHERE id=?""",
              (scope,limit,fi.now(),run_id))
        else:
            run_id=uuid.uuid4().hex[:16]
            c.execute("""INSERT INTO film_scan_runs
              (id,scope,item_limit,status,add_status,updated)
              VALUES(?,?,?,'queued','running',?)""",
              (run_id,scope,limit,fi.now()))
        c.commit()
    return run_id

def stage_batch(run_id):
    with fi.connect() as c:
        rows=c.execute("""SELECT id,external_key,entry_json FROM film_scan_items
          WHERE scan_id=? AND state='discovered' ORDER BY id LIMIT ?""",
          (run_id,BATCH)).fetchall()
        if not rows:
            return 0
        ids=[r["id"] for r in rows]
        c.executemany("UPDATE film_scan_items SET state='staging' WHERE id=? AND state='discovered'",
                      [(i,) for i in ids])
        c.commit()
    entries=[json.loads(r["entry_json"]) for r in rows]
    try:
        catalog.ensure_hosts(entries)
        config=fi.settings()
    except Exception as exc:
        for row in rows:
            with fi.connect() as c:
                c.execute("UPDATE film_scan_items SET state='error',error=? WHERE id=?",
                          (type(exc).__name__,row["id"]))
                c.execute("""UPDATE film_scan_runs SET add_total=add_total+1,
                  add_processed=add_processed+1,add_errors=add_errors+1,updated=?
                  WHERE id=?""",(fi.now(),run_id))
                c.commit()
        print("PIPELINE_HOST_SETUP_ERROR",type(exc).__name__,flush=True)
        return len(rows)
    added=errors=0
    for row,entry in zip(rows,entries):
        try:
            entry["rights_ok"]=True
            entry["silent_import"]=True
            with fi.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                try:
                    count=fi.stage(c,entry,config["hosts"])
                    c.commit()
                except Exception:
                    c.rollback()
                    raise
            state="queued" if count else "already_added"
            added+=count
            error=""
        except Exception as exc:
            state="error"
            error=type(exc).__name__
            errors+=1
        with fi.connect() as c:
            c.execute("UPDATE film_scan_items SET state=?,error=? WHERE id=?",
                      (state,error,row["id"]))
            c.execute("""UPDATE film_scan_runs SET add_total=add_total+1,
              add_processed=add_processed+1,add_variants=add_variants+?,
              add_errors=add_errors+?,updated=? WHERE id=?""",
              (count if state=="queued" else 0,1 if state=="error" else 0,fi.now(),run_id))
            c.commit()
    with fi.connect() as c:
        queue_stats=fi.stats(c)
    print("PIPELINE_STAGED_BATCH",json.dumps({
        "items":len(rows),"variants_added":added,"errors":errors,
        "upload_queue":queue_stats
    },ensure_ascii=False),flush=True)
    return len(rows)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--scope",choices=("all","movie","series"),default="all")
    parser.add_argument("--limit",type=int,default=0,
        help="Items per source; 0 means full archive")
    parser.add_argument("--resume-active",action="store_true")
    args=parser.parse_args()
    if args.limit<0 or args.limit>10000:
        raise SystemExit("limit must be 0..10000")
    config=fi.settings()
    if not config.get("local_bot_api"):
        raise SystemExit("Telegram Local Bot API is not enabled")
    if args.limit>0:
        with fi.connect() as c:
            retried=c.execute("""UPDATE film_import_jobs SET state='pending',
              last_error='',updated=? WHERE state='failed' AND attempts<3
              AND last_error LIKE 'HTTPError:503%' AND updated<?""",
              (fi.now(),fi.now()-86400)).rowcount
            c.commit()
        if retried:
            print("PIPELINE_REQUEUED_TRANSIENT_503",retried,flush=True)
    run_id=reserve_run(args.scope,args.limit,args.resume_active)
    if not run_id:
        return 0
    print("PIPELINE_SCAN_STARTED",json.dumps({
        "scan_id":run_id,"scope":args.scope,"limit_per_source":args.limit,
        "meaning":"full archive" if args.limit==0 else "latest items per source"
    }),flush=True)
    scan_errors=[]
    def scanner():
        try:
            catalog.scan(run_id)
        except Exception as exc:
            scan_errors.append(exc)
            with fi.connect() as c:
                c.execute("""UPDATE film_scan_runs SET status='failed',
                  last_error=?,updated=? WHERE id=?""",
                  (type(exc).__name__,fi.now(),run_id))
                c.commit()
            print("PIPELINE_SCAN_FAILED",type(exc).__name__,flush=True)
    thread=threading.Thread(target=scanner,name="film2media-scanner",daemon=True)
    thread.start()
    while thread.is_alive():
        n=stage_batch(run_id)
        if not n:
            time.sleep(0.5)
    thread.join()
    while stage_batch(run_id):
        pass
    with fi.connect() as c:
        run=c.execute("SELECT * FROM film_scan_runs WHERE id=?",(run_id,)).fetchone()
        item_counts={r[0]:r[1] for r in c.execute(
            "SELECT state,COUNT(*) FROM film_scan_items WHERE scan_id=? GROUP BY state",
            (run_id,))}
        add_total=sum(item_counts.values())
        add_errors=item_counts.get("error",0)
        c.execute("""UPDATE film_scan_runs SET add_status=?,add_total=?,
          add_processed=?,add_errors=?,updated=? WHERE id=?""",
          ("done" if run["status"]=="done" and not scan_errors else "partial",
           add_total,add_total,add_errors,fi.now(),run_id))
        c.commit()
        final=c.execute("SELECT * FROM film_scan_runs WHERE id=?",(run_id,)).fetchone()
        result={k:final[k] for k in (
            "id","scope","item_limit","status","total","processed","discovered",
            "skipped","errors","add_status","add_total","add_processed",
            "add_variants","add_errors","last_error")}
        jobs={r[0]:r[1] for r in c.execute(
            "SELECT state,COUNT(*) FROM film_import_jobs GROUP BY state")}
    print("PIPELINE_FINAL",json.dumps(result,ensure_ascii=False),flush=True)
    print("PIPELINE_UPLOAD_QUEUE",json.dumps(jobs,ensure_ascii=False),flush=True)
    return 1 if scan_errors else 0

if __name__=="__main__":
    raise SystemExit(main())