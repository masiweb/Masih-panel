package controller

import (
 "crypto/rand"
 "encoding/hex"
 "encoding/json"
 "io"
 "net/http"
 "os"
 "os/exec"
 "path/filepath"
 "regexp"
 "sort"
 "strings"
 "time"
 "github.com/gin-gonic/gin"
 "github.com/mhsanaei/3x-ui/v3/internal/web/session"
)

const archiveDirectory="/root/masiha-backups"
const uploadLimit=512<<20
var safeArchive=regexp.MustCompile(`^masiha-full-[a-zA-Z0-9_.-]+\.tar\.gz\.gpg(?:\.sha256)?$`)
var safeIdentifier=regexp.MustCompile(`^[a-f0-9]{32}$`)

func (a *APIController) backupAdmin(c *gin.Context) bool {
 u:=session.GetLoginUser(c)
 main,err:=a.userService.GetFirstUser()
 if err!=nil||u==nil||main==nil||u.Id!=main.Id {
  c.AbortWithStatusJSON(http.StatusForbidden,gin.H{"success":false,"msg":"Administrator only"})
  return false
 }
 c.Header("Cache-Control","private, no-store")
 return true
}
func newBackupID()string{
 var b [16]byte
 if _,err:=rand.Read(b[:]);err!=nil{return ""}
 return hex.EncodeToString(b[:])
}
func launchBackup(id,action string)error{
 if !safeIdentifier.MatchString(id) || (action!="create"&&action!="inspect"&&action!="restore"){
  return os.ErrInvalid
 }
 return exec.Command("systemd-run","--quiet","--collect",
  "--unit=masiha-webbackup-"+action+"-"+id,"--property=Type=exec","/usr/bin/python3",
  "/opt/masiha-vpn-extension/web_backup_worker.py",action,id).Run()
}
func privateRegular(path string)bool{
 fi,err:=os.Lstat(path)
 return err==nil&&fi.Mode().IsRegular()&&fi.Mode().Perm()&0077==0
}
func (a *APIController) backupStatus(c *gin.Context){
 if !a.backupAdmin(c){return}
 files:=[]gin.H{}
 entries,_:=os.ReadDir(archiveDirectory)
 for _,entry:=range entries{
  name:=entry.Name()
  if entry.IsDir()||!safeArchive.MatchString(name)||!strings.HasSuffix(name,".gpg"){continue}
  info,err:=entry.Info();if err!=nil{continue}
  files=append(files,gin.H{"name":name,"size":info.Size(),
   "created":info.ModTime().UTC().Format(time.RFC3339)})
 }
 sort.Slice(files,func(i,j int)bool{return files[i]["created"].(string)>files[j]["created"].(string)})
 jobs:=[]json.RawMessage{}
 found,_:=os.ReadDir(filepath.Join(archiveDirectory,"web-jobs"))
 for _,entry:=range found{
  name:=strings.TrimSuffix(entry.Name(),".json")
  if !strings.HasSuffix(entry.Name(),".json")||!safeIdentifier.MatchString(name){continue}
  content,err:=os.ReadFile(filepath.Join(archiveDirectory,"web-jobs",entry.Name()))
  if err==nil&&json.Valid(content){jobs=append(jobs,content)}
 }
 c.JSON(200,gin.H{"success":true,"obj":gin.H{"backups":files,"jobs":jobs,
  "has_key":privateRegular("/root/.masiha-backup.key")}})
}
func (a *APIController) backupCreate(c *gin.Context){
 if !a.backupAdmin(c){return}
 id:=newBackupID()
 if id==""||launchBackup(id,"create")!=nil {
  c.JSON(503,gin.H{"success":false,"msg":"Backup service is unavailable"});return
 }
 c.JSON(202,gin.H{"success":true,"obj":gin.H{"job":id}})
}
func (a *APIController) backupDownload(c *gin.Context){
 if !a.backupAdmin(c){return}
 name:=c.Param("name")
 if name=="backup-secret.key"{
  const key="/root/.masiha-backup.key"
  if !privateRegular(key){c.AbortWithStatus(404);return}
  c.FileAttachment(key,name);return
 }
 if !safeArchive.MatchString(name){c.AbortWithStatus(404);return}
 path:=filepath.Join(archiveDirectory,name)
 if !privateRegular(path){c.AbortWithStatus(404);return}
 c.FileAttachment(path,name)
}
func saveUploaded(header io.Reader,dest string,max int64)error{
 output,err:=os.OpenFile(dest,os.O_WRONLY|os.O_EXCL|os.O_CREATE,0600)
 if err!=nil{return err}
 defer output.Close()
 n,err:=io.Copy(output,io.LimitReader(header,max+1))
 if err!=nil||n<1||n>max {os.Remove(dest);return os.ErrInvalid}
 return output.Sync()
}
func (a *APIController) backupUpload(c *gin.Context){
 if !a.backupAdmin(c){return}
 c.Request.Body=http.MaxBytesReader(c.Writer,c.Request.Body,uploadLimit)
 if c.Request.ParseMultipartForm(8<<20)!=nil{
  c.JSON(413,gin.H{"success":false,"msg":"Archive is too large"});return
 }
 if c.Request.MultipartForm!=nil{defer c.Request.MultipartForm.RemoveAll()}
 archive,err:=c.FormFile("archive")
 if err!=nil||archive.Size<1024||archive.Size>uploadLimit-4096 {
  c.JSON(400,gin.H{"success":false,"msg":"Invalid archive"});return
 }
 key,err:=c.FormFile("key")
 if err!=nil||key.Size<16||key.Size>4096 {
  c.JSON(400,gin.H{"success":false,"msg":"Select your separate backup key"});return
 }
 id:=newBackupID();if id==""{c.AbortWithStatus(500);return}
 dir:=filepath.Join(archiveDirectory,"web-imports")
 if os.MkdirAll(dir,0700)!=nil{c.AbortWithStatus(500);return}
 saved:=[]string{}
 save:=func(field,extension string,max int64)error{
  f,e:=c.FormFile(field);if e!=nil{return e}
  file,e:=f.Open();if e!=nil{return e}
  defer file.Close()
  path:=filepath.Join(dir,id+extension)
  e=saveUploaded(file,path,max)
  if e==nil{saved=append(saved,path)}
  return e
 }
 err=save("archive",".gpg",uploadLimit-4096)
 if err==nil{err=save("key",".key",4096)}
 if err==nil{err=launchBackup(id,"inspect")}
 if err!=nil{
  for _,path:=range saved{os.Remove(path)}
  c.JSON(400,gin.H{"success":false,"msg":"Import failed"});return
 }
 c.JSON(202,gin.H{"success":true,"obj":gin.H{"job":id}})
}
func (a *APIController) backupRestore(c *gin.Context){
 if !a.backupAdmin(c){return}
 var body struct{Job string `json:"job"`; Confirm string `json:"confirm"`}
 if c.ShouldBindJSON(&body)!=nil||!safeIdentifier.MatchString(body.Job)||body.Confirm!="RESTORE"{
  c.JSON(400,gin.H{"success":false,"msg":"Type RESTORE to confirm"});return
 }
 path:=filepath.Join(archiveDirectory,"web-jobs",body.Job+".json")
 raw,err:=os.ReadFile(path)
 var state struct{State string `json:"state"`}
 if err!=nil||json.Unmarshal(raw,&state)!=nil||state.State!="done"{
  c.JSON(409,gin.H{"success":false,"msg":"Verify the imported archive first"});return
 }
 for _,extension:=range []string{".gpg",".key"}{
  if !privateRegular(filepath.Join(archiveDirectory,"web-imports",body.Job+extension)){
   c.JSON(409,gin.H{"success":false,"msg":"Import files are missing"});return
  }
 }
 if err:=launchBackup(body.Job,"restore");err!=nil{
  c.JSON(503,gin.H{"success":false,"msg":"Could not start restore"});return
 }
 c.JSON(202,gin.H{"success":true,"obj":gin.H{"job":body.Job}})
}
