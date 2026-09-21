const api = "/api/v1";
const view = document.getElementById("view");

async function req(url, options = {}) {
  const response = await fetch(api + url, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
  });
  if (response.status === 401) {
    window.location = "/admin/login";
    throw new Error("authentication required");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    let message = body.detail || "خطا در انجام عملیات";
    const fieldNames = {name:"نام",duration_days:"مدت",traffic_gb:"حجم",price:"قیمت",country_code:"کشور",public_address:"آدرس نود",protocols:"پروتکل"};
    if (Array.isArray(message)) {
      message = message.map((item) => {
        const field = item.loc && item.loc.length ? item.loc[item.loc.length - 1] : "فیلد";
        return (fieldNames[field] || field) + ": " + (item.msg || "مقدار نامعتبر است");
      }).join("\n");
    } else if (typeof message === "object") {
      message = JSON.stringify(message);
    }
    throw new Error(message);
  }
  return body;
}

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));

function showError(error) {
  alert(error.message || "خطای ناشناخته");
}

function bindActions() {
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.onclick = () => {
      const fn = window[button.dataset.action];
      if (typeof fn === "function") fn(button.dataset.id);
    };
  });
}

function actions(type, id) {
  return `<button data-action="edit${type}" data-id="${esc(id)}">ویرایش</button>
    <button class="danger" data-action="del${type}" data-id="${esc(id)}">حذف</button>`;
}

function resourceTable(rows, columns, type) {
  return `<table><thead><tr>
    ${columns.map((column) => `<th>${column[1]}</th>`).join("")}<th>عملیات</th>
    </tr></thead><tbody>
    ${rows.map((row) => `<tr>
      ${columns.map((column) => `<td>${esc(column[2] ? column[2](row[column[0]], row) : row[column[0]])}</td>`).join("")}
      <td>${actions(type, row.id)}</td></tr>`).join("")}
    </tbody></table>`;
}

async function overview() {
  const data = await req("/admin/summary");
  view.innerHTML = `<div class="cards">
    ${[["کاربران", data.users], ["پلن‌ها", data.plans], ["کشورها", data.countries], ["نودها", data.nodes], ["نود آنلاین", data.online_nodes]]
      .map((item) => `<div class="card">${item[0]}<strong>${item[1]}</strong></div>`).join("")}
    </div><div class="panel"><h2>وضعیت هسته مرکزی</h2><p>PostgreSQL، Redis و API مرکزی فعال هستند.<br><br><b>نود داخلی:</b> Xray:8443 — WireGuard:51820 — OpenVPN:1194 — OpenConnect:4443</p></div>`;
}

async function countries() {
  window.countriesData = await req("/admin/countries");
  view.innerHTML = `<div class="panel"><h2>کشورها</h2>
    <form id="countryForm" class="grid-form"><input id="countryCode" maxlength="2" placeholder="کد مانند DE" required>
    <input id="countryName" placeholder="نام کشور" required><button class="primary">افزودن</button></form>
    ${resourceTable(countriesData, [["code","کد"],["name","نام"],["enabled","فعال",(v)=>v?"بله":"خیر"]], "Country")}</div>`;
  countryForm.onsubmit = addCountry;
  bindActions();
}

async function plans() {
  window.plansData = await req("/admin/plans");
  view.innerHTML = `<div class="panel"><h2>پلن‌های فروش</h2><div class="help-box"><b>راهنما:</b> نام پلن باید حداقل دو کاراکتر باشد. مدت برحسب روز، حجم برحسب گیگابایت و قیمت برحسب واحد مالی مورد استفاده شماست.</div>
    <form id="planForm" class="grid-form"><label class="field"><span>نام پلن</span><input id="planName" minlength="2" placeholder="مثال: یک‌ماهه ۵۰ گیگ" required><small>حداقل دو کاراکتر</small></label>
    <label class="field"><span>مدت اعتبار</span><input id="planDays" type="number" min="1" value="30" required><small>تعداد روز</small></label><label class="field"><span>حجم ترافیک</span><input id="planTraffic" type="number" min="1" value="50" required><small>برحسب گیگابایت</small></label>
    <label class="field"><span>قیمت فروش</span><input id="planPrice" type="number" min="0" step="0.01" value="0" required><small>مبلغ فروش</small></label><button class="primary">ساخت</button></form>
    ${resourceTable(plansData, [["name","نام"],["duration_days","مدت"],["traffic_gb","حجم"],["price","قیمت"],["enabled","فعال",(v)=>v?"بله":"خیر"]], "Plan")}</div>`;
  planForm.onsubmit = addPlan;
  bindActions();
}

async function users() {
  const [rows, planRows] = await Promise.all([req("/admin/users"), req("/admin/plans")]);
  window.usersData = rows; window.plansData = planRows;
  view.innerHTML = `<div class="panel"><h2>کاربران</h2>
    <form id="userForm" class="grid-form"><input id="userName" placeholder="نام کاربری" required><input id="userMobile" placeholder="موبایل">
    <select id="userPlan"><option value="">بدون پلن</option>${planRows.map((p)=>`<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select>
    <button class="primary">ساخت</button></form>
    ${resourceTable(rows, [["username","نام کاربری"],["mobile","موبایل"],["plan_id","پلن"],["enabled","فعال",(v)=>v?"بله":"خیر"]], "User")}</div>`;
  userForm.onsubmit = addUser;
  bindActions();
}

async function nodes() {
  const [rows, countryRows] = await Promise.all([req("/nodes"), req("/admin/countries")]);
  window.nodesData = rows; window.countriesData = countryRows;
  view.innerHTML = `<div class="panel"><h2>نودهای VPN</h2><div class="help-box"><b>نود چیست؟</b> نود همان سرور VPN است. نام: یک عنوان داخلی مثل Germany-01؛ کشور: محل سرور؛ IP یا دامنه: آدرس عمومی سرور؛ پروتکل‌ها: xray,wireguard. پس از ثبت، توکن اتصال Agent فقط یک بار نمایش داده می‌شود.</div>
    <form id="nodeForm" class="grid-form"><label class="field"><span>نام نود</span><input id="nodeName" minlength="2" placeholder="مثال: Germany-01" required><small>نام داخلی سرور</small></label>
    <label class="field"><span>کشور سرور</span><select id="nodeCountry">${countryRows.map((c)=>`<option value="${c.code}">${esc(c.name)}</option>`).join("")}</select>
    <input id="nodeAddress" placeholder="IP یا دامنه" required><label class="field"><span>پروتکل‌ها</span><input id="nodeProtocols" value="xray,wireguard" placeholder="xray,wireguard"><small>با کاما جدا کنید: xray, wireguard, openvpn, openconnect</small></label>
    <button class="primary">افزودن</button></form>
    ${resourceTable(rows, [["name","نام"],["country_code","کشور"],["public_address","آدرس"],["protocols","پروتکل"],["status","وضعیت"]], "Node")}</div>`;
  nodeForm.onsubmit = addNode;
  bindActions();
}

async function services() {
  const [rows, userRows, planRows, nodeRows] = await Promise.all([
    req("/admin/services"), req("/admin/users"), req("/admin/plans"), req("/nodes"),
  ]);
  window.servicesData = rows;
  if (!userRows.length || !planRows.length || !nodeRows.length) {
    const missing = [];
    if (!userRows.length) missing.push("کاربر");
    if (!planRows.length) missing.push("پلن");
    if (!nodeRows.length) missing.push("نود");
    view.innerHTML = '<div class="panel"><h2>صدور سرویس VPN</h2><div class="notice warning"><b>پیش‌نیاز ناقص:</b> ابتدا ' + missing.join("، ") + ' را بسازید.</div></div>';
    return;
  }
  view.innerHTML = `<div class="panel"><h2>صدور سرویس VPN</h2>
    <div class="help-box"><b>دریافت کانفیگ:</b> بعد از فعال‌شدن سرویس، «نمایش» برای مشاهده و کپی، «دانلود» برای فایل کلاینت، «QR» برای اسکن و «کپی ساب» برای لینک بروزرسانی خودکار است.</div>
    <form id="serviceForm" class="grid-form">
    <select id="serviceUser">${userRows.map((x)=>`<option value="${x.id}">${esc(x.username)}</option>`).join("")}</select>
    <select id="servicePlan">${planRows.map((x)=>`<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select>
    <select id="serviceNode">${nodeRows.map((x)=>`<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select>
    <select id="serviceProtocol"><option value="xray">Xray / VLESS Reality</option><option value="wireguard">WireGuard</option><option value="openvpn">OpenVPN</option><option value="openconnect">OpenConnect</option></select>
    <button class="primary">صدور سرویس</button></form>
    <div class="table-wrap"><table><thead><tr><th>شناسه</th><th>پروتکل</th><th>وضعیت</th><th>حجم</th><th>انقضا</th><th>کانفیگ و عملیات</th></tr></thead><tbody>
    ${rows.map((x)=>`<tr><td>${esc(x.external_id)}</td><td>${esc(x.protocol)}</td><td><span class="status ${esc(x.status)}">${esc(x.status)}</span></td>
    <td>${Math.round(x.quota_bytes/1073741824)} GB</td><td>${esc(new Date(x.expires_at).toLocaleDateString("fa-IR"))}</td>
    <td class="service-actions">${x.has_config ? `<button data-action="showServiceConfig" data-id="${x.id}">نمایش</button><button data-action="downloadService" data-id="${x.id}">دانلود</button><button data-action="showServiceQr" data-id="${x.id}">QR</button><button data-action="copySubscription" data-id="${x.id}">کپی ساب</button>` : `<button data-action="reprovisionService" data-id="${x.id}">ساخت مجدد</button>`}
    <button data-action="renewService" data-id="${x.id}">تمدید</button><button class="danger" data-action="revokeService" data-id="${x.id}">قطع</button></td></tr>`).join("")}</tbody></table></div></div>`;
  serviceForm.onsubmit = addService;
  bindActions();
}

function serviceModal(title) {
  let modal = document.getElementById("serviceModal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "serviceModal";
    modal.className = "modal-overlay";
    modal.innerHTML = '<div class="modal-card"><button id="modalClose" class="modal-close">×</button><h3 id="modalTitle"></h3><div id="modalBody"></div></div>';
    document.body.appendChild(modal);
    modalClose.onclick = () => modal.remove();
    modal.onclick = (event) => { if (event.target === modal) modal.remove(); };
  }
  modalTitle.textContent = title;
  modalBody.replaceChildren();
  return modalBody;
}

window.showServiceConfig = async (id) => {
  try {
    const item = await req("/admin/services/" + id);
    if (!item.client_config) throw new Error("کانفیگ هنوز آماده نیست");
    const body = serviceModal("کانفیگ " + item.protocol);
    const pre = document.createElement("pre"); pre.className = "config-box"; pre.textContent = item.client_config;
    const copy = document.createElement("button"); copy.className = "primary"; copy.textContent = "کپی کانفیگ";
    copy.onclick = async () => { await navigator.clipboard.writeText(item.client_config); copy.textContent = "کپی شد ✓"; };
    body.append(pre, copy);
  } catch (e) { showError(e); }
};
window.downloadService = (id) => { window.location = api + "/admin/services/" + id + "/download"; };
window.showServiceQr = (id) => {
  const body = serviceModal("QR کانفیگ");
  const image = document.createElement("img"); image.className = "qr-image"; image.alt = "QR کانفیگ VPN";
  image.src = api + "/admin/services/" + id + "/qr?t=" + Date.now();
  body.appendChild(image);
};
window.copySubscription = async (id) => {
  try {
    const item = servicesData.find((x) => x.id === id);
    if (!item || !item.subscription_url) throw new Error("لینک ساب آماده نیست");
    await navigator.clipboard.writeText(item.subscription_url);
    alert("لینک Subscription کپی شد");
  } catch (e) { showError(e); }
};
window.reprovisionService = async (id) => {
  try { await req("/admin/services/" + id + "/reprovision", {method:"POST"}); alert("در صف ساخت قرار گرفت"); services(); }
  catch (e) { showError(e); }
};


async function orders() {
  const [rows, userRows, planRows] = await Promise.all([req("/admin/orders"), req("/admin/users"), req("/admin/plans")]);
  view.innerHTML = `<div class="panel"><h2>سفارش‌ها</h2><form id="orderForm" class="grid-form">
    <select id="orderUser">${userRows.map((x)=>`<option value="${x.id}">${esc(x.username)}</option>`).join("")}</select>
    <select id="orderPlan">${planRows.map((x)=>`<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select>
    <button class="primary">ثبت سفارش</button></form><table><thead><tr><th>شناسه</th><th>مبلغ</th><th>وضعیت</th><th>منبع</th><th>عملیات</th></tr></thead><tbody>
    ${rows.map((x)=>`<tr><td>${esc(x.id)}</td><td>${esc(x.amount)}</td><td>${esc(x.status)}</td><td>${esc(x.source)}</td>
    <td>${x.status==="pending"?`<button data-action="payOrder" data-id="${x.id}">تأیید پرداخت</button>`:""}</td></tr>`).join("")}</tbody></table></div>`;
  orderForm.onsubmit = addOrder;
  bindActions();
}

async function logs() {
  const rows = await req("/admin/audit-logs");
  view.innerHTML = `<div class="panel"><h2>گزارش فعالیت مدیران</h2><table><thead><tr><th>زمان</th><th>مدیر</th><th>عملیات</th><th>بخش</th><th>شناسه</th></tr></thead><tbody>
    ${rows.map((x)=>`<tr><td>${esc(new Date(x.created_at).toLocaleString("fa-IR"))}</td><td>${esc(x.admin_username)}</td>
    <td>${esc(x.action)}</td><td>${esc(x.resource_type)}</td><td>${esc(x.resource_id)}</td></tr>`).join("")}</tbody></table></div>`;
}

async function settings() {
  view.innerHTML = `<div class="panel"><h2>تغییر رمز مدیر</h2><form id="passwordForm" class="grid-form">
    <input id="currentPassword" type="password" placeholder="رمز فعلی" required>
    <input id="newPassword" type="password" minlength="12" placeholder="رمز جدید حداقل ۱۲ کاراکتر" required>
    <button class="primary">تغییر رمز</button></form><p id="securityMessage"></p></div>`;
  passwordForm.onsubmit = changePassword;
}

async function addCountry(event){event.preventDefault();try{await req("/admin/countries",{method:"POST",body:JSON.stringify({code:countryCode.value,name:countryName.value})});countries()}catch(e){showError(e)}}
async function addPlan(event){event.preventDefault();try{await req("/admin/plans",{method:"POST",body:JSON.stringify({name:planName.value,duration_days:+planDays.value,traffic_gb:+planTraffic.value,price:+planPrice.value})});plans()}catch(e){showError(e)}}
async function addUser(event){event.preventDefault();try{await req("/admin/users",{method:"POST",body:JSON.stringify({username:userName.value,mobile:userMobile.value||null,plan_id:userPlan.value||null})});users()}catch(e){showError(e)}}
async function addNode(event){event.preventDefault();try{const result=await req("/nodes",{method:"POST",body:JSON.stringify({name:nodeName.value,country_code:nodeCountry.value,public_address:nodeAddress.value,protocols:nodeProtocols.value.split(",").map((x)=>x.trim())})});alert(`توکن نود فقط یک بار نمایش داده می‌شود. آن را ذخیره کنید:\n\n${result.node_token}`);nodes()}catch(e){showError(e)}}
async function addService(event){event.preventDefault();try{await req("/admin/services",{method:"POST",body:JSON.stringify({subscriber_id:serviceUser.value,plan_id:servicePlan.value,node_id:serviceNode.value,protocol:serviceProtocol.value})});services()}catch(e){showError(e)}}
async function addOrder(event){event.preventDefault();try{await req("/admin/orders",{method:"POST",body:JSON.stringify({subscriber_id:orderUser.value,plan_id:orderPlan.value,source:"admin"})});orders()}catch(e){showError(e)}}

window.editCountry=async(id)=>{const x=countriesData.find((v)=>v.id===id),name=prompt("نام کشور",x.name);if(name!==null){await req("/admin/countries/"+id,{method:"PATCH",body:JSON.stringify({name})});countries()}};
window.editPlan=async(id)=>{const x=plansData.find((v)=>v.id===id),name=prompt("نام پلن",x.name);if(name!==null){await req("/admin/plans/"+id,{method:"PATCH",body:JSON.stringify({name})});plans()}};
window.editUser=async(id)=>{const x=usersData.find((v)=>v.id===id),mobile=prompt("شماره موبایل",x.mobile||"");if(mobile!==null){await req("/admin/users/"+id,{method:"PATCH",body:JSON.stringify({mobile:mobile||null})});users()}};
window.editNode=async(id)=>{const x=nodesData.find((v)=>v.id===id),address=prompt("IP یا دامنه",x.public_address);if(address!==null){await req("/nodes/"+id,{method:"PATCH",body:JSON.stringify({public_address:address})});nodes()}};
window.delCountry=async(id)=>{if(confirm("حذف شود؟")){await req("/admin/countries/"+id,{method:"DELETE"});countries()}};
window.delPlan=async(id)=>{if(confirm("حذف شود؟")){await req("/admin/plans/"+id,{method:"DELETE"});plans()}};
window.delUser=async(id)=>{if(confirm("حذف شود؟")){await req("/admin/users/"+id,{method:"DELETE"});users()}};
window.delNode=async(id)=>{if(confirm("حذف شود؟")){await req("/nodes/"+id,{method:"DELETE"});nodes()}};
window.renewService=async(id)=>{if(confirm("سرویس تمدید شود؟")){await req("/admin/services/"+id+"/renew",{method:"POST",body:"{}"});services()}};
window.revokeService=async(id)=>{if(confirm("سرویس قطع شود؟")){await req("/admin/services/"+id+"/revoke",{method:"POST"});services()}};
window.payOrder=async(id)=>{if(confirm("پرداخت تأیید شود؟")){await req("/admin/orders/"+id+"/paid",{method:"POST"});orders()}};

async function changePassword(event){event.preventDefault();try{await req("/auth/change-password",{method:"POST",body:JSON.stringify({current_password:currentPassword.value,new_password:newPassword.value})});securityMessage.textContent="رمز تغییر کرد؛ در حال انتقال...";setTimeout(()=>location="/admin/login",1000)}catch(e){securityMessage.textContent=e.message}}
async function logout(){await req("/auth/logout",{method:"POST"});location="/admin/login"}


const protocolPorts = {xray:8443, wireguard:51820, openvpn:1194, openconnect:4443};
function inboundFields(protocol) {
  if (protocol === "xray") return `<label class="field"><span>نوع Xray</span><select id="ibVariant"><option value="vless">VLESS</option><option value="vmess">VMess</option><option value="trojan">Trojan</option><option value="shadowsocks">Shadowsocks</option></select></label><label class="field"><span>Transport</span><select id="ibTransport"><option value="tcp">TCP/RAW</option><option value="ws">WebSocket</option><option value="grpc">gRPC</option><option value="httpupgrade">HTTPUpgrade</option><option value="xhttp">XHTTP</option><option value="kcp">mKCP</option></select></label><label class="field"><span>Security</span><select id="ibSecurity"><option value="reality">REALITY</option><option value="tls">TLS</option><option value="none">None</option></select></label><label class="field"><span>Server Name / SNI</span><input id="ibServerName" value="www.microsoft.com"></label><label class="field"><span>Flow</span><input id="ibFlow" value="xtls-rprx-vision"></label><label class="field"><span>Path / Service name</span><input id="ibPath" placeholder="/vpn"></label><label class="check"><input id="ibSniffing" type="checkbox" checked> Sniffing</label>`;
  if (protocol === "wireguard") return `<label class="field"><span>Interface</span><input id="ibInterface" value="wg0"></label><label class="field"><span>شبکه تونل</span><input id="ibNetwork" value="10.70.0.0/24"></label><label class="field"><span>MTU</span><input id="ibMtu" type="number" value="1420"></label><label class="field"><span>DNS</span><input id="ibDns" value="1.1.1.1,1.0.0.1"></label><label class="field"><span>Allowed IPs</span><input id="ibAllowedIps" value="0.0.0.0/0,::/0"></label><label class="field"><span>Keepalive</span><input id="ibKeepalive" type="number" value="25"></label>`;
  if (protocol === "openvpn") return `<label class="field"><span>Transport</span><select id="ibOvpnTransport"><option value="udp">UDP</option><option value="tcp">TCP</option></select></label><label class="field"><span>Device</span><select id="ibDevice"><option value="tun">TUN</option><option value="tap">TAP</option></select></label><label class="field"><span>شبکه تونل</span><input id="ibNetwork" value="10.71.0.0/24"></label><label class="field"><span>Cipher</span><input id="ibCipher" value="AES-256-GCM"></label><label class="field"><span>Auth</span><input id="ibAuth" value="SHA256"></label><label class="field"><span>DNS</span><input id="ibDns" value="1.1.1.1,1.0.0.1"></label>`;
  return `<label class="field"><span>شبکه تونل</span><input id="ibNetwork" value="10.72.0.0/24"></label><label class="field"><span>حداکثر Client</span><input id="ibMaxClients" type="number" value="256"></label><label class="field"><span>اتصال هم‌زمان هر Client</span><input id="ibMaxSame" type="number" value="4"></label><label class="field"><span>DNS</span><input id="ibDns" value="1.1.1.1,1.0.0.1"></label><label class="check"><input id="ibDtls" type="checkbox" checked> DTLS فعال</label><label class="check"><input id="ibCisco" type="checkbox" checked> سازگار با Cisco AnyConnect</label>`;
}
function collectInboundSettings(protocol) {
  let settings = {};
  if (protocol === "xray") settings = {variant:ibVariant.value,transport:ibTransport.value,security:ibSecurity.value,server_name:ibServerName.value,flow:ibFlow.value,path:ibPath.value,sniffing:ibSniffing.checked,fingerprint:"chrome"};
  if (protocol === "wireguard") settings = {interface:ibInterface.value,network:ibNetwork.value,mtu:+ibMtu.value,dns:ibDns.value.split(",").map(x=>x.trim()),allowed_ips:ibAllowedIps.value.split(",").map(x=>x.trim()),persistent_keepalive:+ibKeepalive.value};
  if (protocol === "openvpn") settings = {transport:ibOvpnTransport.value,device:ibDevice.value,network:ibNetwork.value,cipher:ibCipher.value,auth:ibAuth.value,dns:ibDns.value.split(",").map(x=>x.trim()),tls_mode:"tls-auth",redirect_gateway:true};
  if (protocol === "openconnect") settings = {network:ibNetwork.value,max_clients:+ibMaxClients.value,max_same_clients:+ibMaxSame.value,dns:ibDns.value.split(",").map(x=>x.trim()),dtls:ibDtls.checked,cisco_compatible:ibCisco.checked};
  const extra = ibAdvanced.value.trim() ? JSON.parse(ibAdvanced.value) : {};
  return {...settings,...extra};
}
async function inbounds() {
  const [rows,nodes] = await Promise.all([req("/admin/inbounds"),req("/nodes")]); window.inboundsData=rows;
  view.innerHTML=`<div class="panel"><h2>Inboundها</h2><div class="help-box"><b>Inbound</b> شنونده واقعی روی یک Node است. پروتکل، پورت، Transport، امنیت و تمام تنظیمات سرور از اینجا کنترل می‌شود.</div><form id="inboundForm"><div class="grid-form"><label class="field"><span>نام</span><input id="ibName" required placeholder="Germany-VLESS-Reality"></label><label class="field"><span>Node</span><select id="ibNode">${nodes.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select></label><label class="field"><span>پروتکل</span><select id="ibProtocol"><option value="xray">Xray</option><option value="wireguard">WireGuard</option><option value="openvpn">OpenVPN</option><option value="openconnect">OpenConnect</option></select></label><label class="field"><span>Listen</span><input id="ibListen" value="0.0.0.0"></label><label class="field"><span>Port</span><input id="ibPort" type="number" value="8443" min="1" max="65535"></label><label class="field"><span>Public Host</span><input id="ibPublicHost" placeholder="xu.chanelchat.ir"></label></div><h3>تنظیمات پروتکل</h3><div id="ibProtocolFields" class="grid-form">${inboundFields("xray")}</div><details><summary>تنظیمات پیشرفته JSON</summary><textarea id="ibAdvanced" class="config-box" placeholder='{"sockopt":{}}'></textarea></details><button class="primary">ساخت Inbound</button></form><div class="table-wrap"><table><thead><tr><th>نام</th><th>Node</th><th>پروتکل</th><th>Listen/Port</th><th>وضعیت</th><th>عملیات</th></tr></thead><tbody>${rows.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(nodes.find(n=>n.id===x.node_id)?.name||x.node_id)}</td><td>${esc(x.protocol)}</td><td dir="ltr">${esc(x.listen)}:${esc(x.port)}</td><td>${x.runtime?.clients_enabled||0} / ${x.runtime?.clients_total||0}</td><td><span class="status ${esc(x.runtime?.last_job?.status||"pending")}">${esc(x.runtime?.last_job?.status||"-")}</span></td><td><span class="status ${esc(x.status)}">${esc(x.status)}</span></td><td><button data-action="editInbound" data-id="${x.id}">ویرایش کامل</button><button class="danger" data-action="deleteInbound" data-id="${x.id}">حذف</button></td></tr>`).join("")}</tbody></table></div></div>`;
  ibProtocol.onchange=()=>{ibPort.value=protocolPorts[ibProtocol.value];ibProtocolFields.innerHTML=inboundFields(ibProtocol.value)};
  inboundForm.onsubmit=async(e)=>{e.preventDefault();try{await req("/admin/inbounds",{method:"POST",body:JSON.stringify({node_id:ibNode.value,name:ibName.value,protocol:ibProtocol.value,listen:ibListen.value,port:+ibPort.value,public_host:ibPublicHost.value||null,settings:collectInboundSettings(ibProtocol.value)})});inbounds()}catch(err){showError(err)}}; bindActions();
}
window.editInbound=async(id)=>{const x=inboundsData.find(v=>v.id===id);const raw=prompt("تنظیمات کامل Inbound را به‌صورت JSON ویرایش کنید",JSON.stringify({name:x.name,listen:x.listen,port:x.port,public_host:x.public_host,enabled:x.enabled,settings:x.settings},null,2));if(raw!==null){try{await req("/admin/inbounds/"+id,{method:"PATCH",body:JSON.stringify(JSON.parse(raw))});inbounds()}catch(e){showError(e)}}};
window.deleteInbound=async(id)=>{if(confirm("Inbound حذف شود؟ فقط وقتی Client متصل ندارد امکان‌پذیر است.")){try{await req("/admin/inbounds/"+id,{method:"DELETE"});inbounds()}catch(e){showError(e)}}};
async function clients() {
  const [rows,inboundRows,userRows,planRows]=await Promise.all([req("/admin/clients"),req("/admin/inbounds"),req("/admin/users"),req("/admin/plans")]);window.clientsData=rows;
  view.innerHTML=`<div class="panel"><h2>Clientها</h2><div class="help-box">هر Client می‌تواند هم‌زمان به چند Inbound و چند پروتکل متصل باشد و فقط یک لینک Subscription مشترک دارد.</div><form id="clientForm" class="grid-form"><label class="field"><span>نام Client</span><input id="clName" required></label><label class="field"><span>کاربر</span><select id="clUser">${userRows.map(x=>`<option value="${x.id}">${esc(x.username)}</option>`).join("")}</select></label><label class="field"><span>پلن</span><select id="clPlan">${planRows.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select></label><label class="field"><span>Inboundها</span><select id="clInbounds" multiple size="${Math.min(8,Math.max(3,inboundRows.length))}">${inboundRows.filter(x=>x.enabled).map(x=>`<option value="${x.id}">${esc(x.name)} — ${esc(x.protocol)}</option>`).join("")}</select></label><label class="field"><span>محدودیت IP</span><input id="clLimitIp" type="number" value="0"></label><label class="field"><span>توضیح</span><input id="clComment"></label><button class="primary">ساخت Client و اتصال</button></form><div class="table-wrap"><table><thead><tr><th>Client</th><th>وضعیت</th><th>حجم</th><th>Inboundها</th><th>Subscription</th><th>عملیات</th></tr></thead><tbody>${rows.map(x=>`<tr><td>${esc(x.name)}</td><td>${x.enabled?"فعال":"غیرفعال"}</td><td>${esc(x.used_gb)} / ${esc(x.quota_gb)} GB</td><td>${x.attachments.map(a=>`${esc(a.inbound_name)} (${esc(a.status)})`).join("<br>")}</td><td><button data-action="copyClientSub" data-id="${x.id}">کپی ساب</button><button data-action="openClientProfile" data-id="${x.id}">صفحه Client</button></td><td><button data-action="clientConfigs" data-id="${x.id}">کانفیگ‌ها و QR</button><button data-action="editClient" data-id="${x.id}">ویرایش اتصال‌ها</button><button data-action="rotateClientSub" data-id="${x.id}">تعویض لینک</button></td></tr>`).join("")}</tbody></table></div></div>`;
  clientForm.onsubmit=async(e)=>{e.preventDefault();const ids=[...clInbounds.selectedOptions].map(x=>x.value);if(!ids.length)return showError(new Error("حداقل یک Inbound انتخاب کنید"));try{await req("/admin/clients",{method:"POST",body:JSON.stringify({name:clName.value,subscriber_id:clUser.value,plan_id:clPlan.value,inbound_ids:ids,limit_ip:+clLimitIp.value,comment:clComment.value||null})});clients()}catch(err){showError(err)}};bindActions();
}
window.copyClientSub=async(id)=>{const x=clientsData.find(v=>v.id===id);await navigator.clipboard.writeText(x.subscription_url);alert("لینک Subscription کپی شد")};
window.openClientProfile=id=>{const x=clientsData.find(v=>v.id===id);window.open(x.profile_url,"_blank")};
window.rotateClientSub=async(id)=>{if(confirm("لینک قبلی فوراً از کار می‌افتد. ادامه؟")){await req("/admin/clients/"+id+"/rotate-subscription",{method:"POST"});clients()}};
window.editClient=async(id)=>{const x=clientsData.find(v=>v.id===id);const all=await req("/admin/inbounds");const current=x.attachments.map(a=>a.inbound_id);const selected=prompt("شناسه Inboundهای متصل را با کاما جدا کنید:\n"+all.map(i=>i.id+" = "+i.name).join("\n"),current.join(","));if(selected!==null){await req("/admin/clients/"+id,{method:"PATCH",body:JSON.stringify({inbound_ids:selected.split(",").map(v=>v.trim()).filter(Boolean)})});clients()}};
window.clientConfigs=async(id)=>{const x=await req("/admin/clients/"+id);const body=serviceModal("کانفیگ‌های "+x.name);x.attachments.forEach(a=>{const card=document.createElement("div");card.className="config-card";const h=document.createElement("h3");h.textContent=a.inbound_name+" — "+a.protocol;card.appendChild(h);if(a.client_config){const pre=document.createElement("pre");pre.className="config-box";pre.textContent=a.client_config;const copy=document.createElement("button");copy.textContent="کپی";copy.onclick=()=>navigator.clipboard.writeText(a.client_config);const download=document.createElement("button");download.textContent="دانلود";download.onclick=()=>location=api+"/admin/client-configs/"+a.id+"/download";const qr=document.createElement("button");qr.textContent="QR";qr.onclick=()=>window.open(api+"/admin/client-configs/"+a.id+"/qr","_blank");card.append(pre,copy,download,qr)}else{card.append("کانفیگ در حال ساخت است")};body.appendChild(card)})};

const views={overview,users,plans,countries,nodes,inbounds,clients,services,orders,logs,settings};
document.querySelectorAll("nav button").forEach((button)=>{
  button.onclick=async()=>{document.querySelectorAll("nav button").forEach((x)=>x.classList.remove("active"));button.classList.add("active");try{await views[button.dataset.view]()}catch(e){showError(e)}};
});
document.querySelector("nav button").click();

/* Masiha inbound modal v0.8.0 */
const imEl=id=>document.getElementById(id), imV=(id,d="")=>imEl(id)?.value??d, imC=id=>!!imEl(id)?.checked;
const imCsv=id=>imV(id).split(",").map(x=>x.trim()).filter(Boolean);
const imF=(l,id,v="",t="text")=>`<label class="im-field"><span>${l}</span><input id="${id}" type="${t}" value="${esc(v)}"></label>`;
const imS=(l,id,a,v)=>`<label class="im-field"><span>${l}</span><select id="${id}">${a.map(x=>`<option value="${x[0]}" ${x[0]===v?"selected":""}>${x[1]}</option>`).join("")}</select></label>`;
const imB=(l,id,v=true)=>`<label class="im-switch"><span>${l}</span><input id="${id}" type="checkbox" ${v?"checked":""}><i></i></label>`;
const protocols=[["xray","Xray"],["wireguard","WireGuard"],["openvpn","OpenVPN"],["openconnect","OpenConnect"]];

function imProtocol(p,s={}){
 if(p==="xray")return `${imS("نوع پروتکل","imVariant",[["vless","VLESS"],["vmess","VMess"],["trojan","Trojan"],["shadowsocks","Shadowsocks"]],s.variant||"vless")}${imF("رمزگشایی","imDecryption",s.decryption||"none")}${imF("رمزنگاری","imEncryption",s.encryption||"none")}${imS("الگوریتم کلید","imKeyAlgorithm",[["x25519-native","X25519 (native)"],["x25519-xorpub","X25519 (xorpub)"],["x25519-random","X25519 (random)"],["mlkem768-native","ML-KEM-768 (native)"],["mlkem768-xorpub","ML-KEM-768 (xorpub)"],["mlkem768-random","ML-KEM-768 (random)"]],s.key_algorithm||"x25519-native")}${imF("Flow","imFlow",s.flow||"xtls-rprx-vision")}<div class="im-field im-wide"><span>کلیدهای Reality</span><div class="im-inline"><input id="imPrivateKey" value="${esc(s.private_key||"")}" placeholder="Private Key"><input id="imPublicKey" value="${esc(s.public_key||"")}" placeholder="Public Key"><button type="button" id="imKeys">تولید</button><button type="button" id="imClearKeys" class="danger">پاک‌کردن</button></div></div>`;
 if(p==="wireguard")return `${imF("Interface","imInterface",s.interface||"wg0")}${imF("شبکه تونل","imNetwork",s.network||"10.70.0.0/24")}${imF("MTU","imMtu",s.mtu||1420,"number")}${imF("DNS","imDns",(s.dns||["1.1.1.1","1.0.0.1"]).join(","))}${imF("Allowed IPs","imAllowed",(s.allowed_ips||["0.0.0.0/0","::/0"]).join(","))}${imF("Keepalive","imKeepalive",s.persistent_keepalive||25,"number")}`;
 if(p==="openvpn")return `${imS("Transport","imOTransport",[["udp","UDP"],["tcp","TCP"]],s.transport||"udp")}${imS("Device","imDevice",[["tun","TUN"],["tap","TAP"]],s.device||"tun")}${imF("شبکه تونل","imNetwork",s.network||"10.71.0.0/24")}${imF("Cipher","imCipher",s.cipher||"AES-256-GCM")}${imF("Auth","imAuth",s.auth||"SHA256")}${imF("DNS","imDns",(s.dns||["1.1.1.1"]).join(","))}${imS("TLS Mode","imTlsMode",[["tls-auth","tls-auth"],["tls-crypt","tls-crypt"],["none","none"]],s.tls_mode||"tls-auth")}${imB("Redirect Gateway","imRedirect",s.redirect_gateway!==false)}`;
 return `${imF("شبکه تونل","imNetwork",s.network||"10.72.0.0/24")}${imF("حداکثر Client","imMaxClients",s.max_clients||256,"number")}${imF("اتصال هم‌زمان","imMaxSame",s.max_same_clients||4,"number")}${imF("DNS","imDns",(s.dns||["1.1.1.1"]).join(","))}${imB("DTLS فعال","imDtls",s.dtls!==false)}${imB("Cisco AnyConnect","imCisco",s.cisco_compatible!==false)}`;
}
function imTransport(s={}){
 const q=s.transport_settings||{},o=q.sockopt||{};
 return `<div class="im-grid">${imS("راه انتقال","imTransport",[["tcp","RAW / TCP"],["kcp","mKCP"],["ws","WebSocket"],["grpc","gRPC"],["httpupgrade","HTTPUpgrade"],["xhttp","XHTTP"]],s.transport||"tcp")}${imB("Proxy Protocol","imProxy",!!q.proxy_protocol)}${imF("Path","imPath",s.path||q.path||"")}${imF("Host","imHost",q.host||"")}${imF("Service Name","imService",q.service_name||"")}${imF("HTTP Obfuscation","imObfs",q.http_obfuscation||"none")}${imF("Header Type","imHeader",q.header_type||"none")}${imF("Seed / Key","imSeed",q.seed||"")}${imF("TCP Masks","imMasks",(q.tcp_masks||[]).join(","))}${imF("Socket Mark","imMark",o.mark||0,"number")}${imF("TCP Keepalive","imTcpKeep",o.tcp_keepalive_interval||0,"number")}${imF("TCP Congestion","imCongestion",o.tcp_congestion||"")}${imB("TCP Fast Open","imTfo",!!o.tcp_fast_open)}${imB("Accept Proxy Protocol","imAcceptProxy",!!q.accept_proxy_protocol)}</div>`;
}
function imSecurity(s={}){
 const q=s.security_settings||{},sec=s.security||"reality";
 return `<div class="im-pills">${["none","tls","reality"].map(x=>`<button type="button" data-sec="${x}" class="${x===sec?"active":""}">${x.toUpperCase()}</button>`).join("")}</div><div class="im-grid">${imF("Server Name / SNI","imSni",s.server_name||q.server_name||"www.microsoft.com")}${imS("Fingerprint / uTLS","imFingerprint",[["chrome","Chrome"],["firefox","Firefox"],["safari","Safari"],["random","Random"],["none","None"]],s.fingerprint||q.fingerprint||"chrome")}${imS("Min TLS","imMinTls",[["1.0","TLS 1.0"],["1.1","TLS 1.1"],["1.2","TLS 1.2"],["1.3","TLS 1.3"]],q.min_tls||"1.2")}${imS("Max TLS","imMaxTls",[["1.2","TLS 1.2"],["1.3","TLS 1.3"]],q.max_tls||"1.3")}${imF("Cipher Suites","imSuites",q.cipher_suites||"")}${imF("ALPN","imAlpn",(q.alpn||["h2","http/1.1"]).join(","))}${imF("Reality Dest","imDest",q.dest||"www.microsoft.com:443")}${imF("Xver","imXver",q.xver||0,"number")}${imF("Short IDs","imShortIds",(q.short_ids||[]).join(","))}${imF("SpiderX","imSpider",q.spider_x||"/")}${imF("Max Time Diff (ms)","imTimeDiff",q.max_time_diff||0,"number")}${imF("Certificate File","imCert",q.cert_file||"")}${imF("Private Key File","imCertKey",q.cert_key||"")}${imF("OCSP Stapling","imOcsp",q.ocsp_stapling||0,"number")}${imF("Verify Names","imVerify",(q.verify_names||[]).join(","))}${imB("Reject Unknown SNI","imReject",!!q.reject_unknown_sni)}${imB("System Root Certificates","imRoots",q.system_roots!==false)}${imB("Session Resumption","imResume",q.session_resumption!==false)}${imB("Allow Insecure","imInsecure",!!q.allow_insecure)}${imB("ECH فعال","imEch",!!q.ech)}${imF("ECH Config","imEchConfig",q.ech_config||"")}${imF("ML-DSA Seed","imMldsaSeed",q.mldsa_seed||"")}${imF("ML-DSA Verify","imMldsaVerify",q.mldsa_verify||"")}${imF("Post-Quantum Signature","imPq",q.pq_signature||"")}${imF("Hybrid Certificate","imHybrid",q.hybrid_certificate||"")}</div>`;
}
function imSniff(s={}){
 const q=typeof s.sniffing==="object"?s.sniffing:{enabled:s.sniffing!==false};
 return `<div class="im-grid">${imB("شنود فعال","imSniff",q.enabled!==false)}${imF("Destination Override","imOverride",(q.dest_override||["http","tls","quic"]).join(","))}${imB("Route Only","imRoute",!!q.route_only)}${imB("Metadata Only","imMetadata",!!q.metadata_only)}${imF("Domains Excluded","imDomains",(q.domains_excluded||[]).join(","))}${imF("IPs Excluded","imIps",(q.ips_excluded||[]).join(","))}</div>`;
}
function imCollect(p,s){
 let r={...s};
 if(p==="xray"){r={...r,variant:imV("imVariant"),decryption:imV("imDecryption"),encryption:imV("imEncryption"),key_algorithm:imV("imKeyAlgorithm"),private_key:imV("imPrivateKey"),public_key:imV("imPublicKey"),flow:imV("imFlow"),transport:imV("imTransport"),path:imV("imPath"),security:document.querySelector("[data-sec].active")?.dataset.sec||"none",server_name:imV("imSni"),fingerprint:imV("imFingerprint")};r.transport_settings={proxy_protocol:imC("imProxy"),path:imV("imPath"),host:imV("imHost"),service_name:imV("imService"),http_obfuscation:imV("imObfs"),header_type:imV("imHeader"),seed:imV("imSeed"),tcp_masks:imCsv("imMasks"),accept_proxy_protocol:imC("imAcceptProxy"),sockopt:{mark:+imV("imMark"),tcp_keepalive_interval:+imV("imTcpKeep"),tcp_congestion:imV("imCongestion"),tcp_fast_open:imC("imTfo")}};r.security_settings={server_name:imV("imSni"),fingerprint:imV("imFingerprint"),min_tls:imV("imMinTls"),max_tls:imV("imMaxTls"),cipher_suites:imV("imSuites"),alpn:imCsv("imAlpn"),dest:imV("imDest"),xver:+imV("imXver"),short_ids:imCsv("imShortIds"),spider_x:imV("imSpider"),max_time_diff:+imV("imTimeDiff"),cert_file:imV("imCert"),cert_key:imV("imCertKey"),ocsp_stapling:+imV("imOcsp"),verify_names:imCsv("imVerify"),reject_unknown_sni:imC("imReject"),system_roots:imC("imRoots"),session_resumption:imC("imResume"),allow_insecure:imC("imInsecure"),ech:imC("imEch"),ech_config:imV("imEchConfig"),mldsa_seed:imV("imMldsaSeed"),mldsa_verify:imV("imMldsaVerify"),pq_signature:imV("imPq"),hybrid_certificate:imV("imHybrid")};r.sniffing={enabled:imC("imSniff"),dest_override:imCsv("imOverride"),route_only:imC("imRoute"),metadata_only:imC("imMetadata"),domains_excluded:imCsv("imDomains"),ips_excluded:imCsv("imIps")};}
 else if(p==="wireguard")r={...r,interface:imV("imInterface"),network:imV("imNetwork"),mtu:+imV("imMtu"),dns:imCsv("imDns"),allowed_ips:imCsv("imAllowed"),persistent_keepalive:+imV("imKeepalive")};
 else if(p==="openvpn")r={...r,transport:imV("imOTransport"),device:imV("imDevice"),network:imV("imNetwork"),cipher:imV("imCipher"),auth:imV("imAuth"),dns:imCsv("imDns"),tls_mode:imV("imTlsMode"),redirect_gateway:imC("imRedirect")};
 else r={...r,network:imV("imNetwork"),max_clients:+imV("imMaxClients"),max_same_clients:+imV("imMaxSame"),dns:imCsv("imDns"),dtls:imC("imDtls"),cisco_compatible:imC("imCisco")};
 const raw=imV("imJson").trim();if(raw)r={...JSON.parse(raw),...r};return r;
}
window.openInboundModal=function(id=null,preset=null){
 const x=id?inboundsData.find(v=>v.id===id):null,s=x?.settings||{},p=x?.protocol||preset||"xray";document.getElementById("inboundModal")?.remove();
 const m=document.createElement("div");m.id="inboundModal";m.className="modal-overlay inbound-overlay";m.innerHTML=`<form id="imForm" class="inbound-modal"><header><div><h2>${x?"ویرایش ورودی":"افزودن ورودی"}</h2><small>تمام تنظیمات استاندارد از طریق فیلدها قابل ویرایش است</small></div><button type="button" id="imClose" class="modal-close">×</button></header><nav class="im-tabs">${[["base","پایه"],["protocol","پروتکل"],["transport","انتقال"],["security","امنیت"],["sniffing","شنود"],["advanced","پیشرفته"]].map((a,i)=>`<button type="button" data-tab="${a[0]}" class="${i?"":"active"}">${a[1]}</button>`).join("")}</nav><main>
 <section class="im-pane active" data-pane="base"><div class="im-grid">${imB("فعال","imEnabled",x?.enabled!==false)}${imF("نام","imName",x?.name||"")}${imS("استقرار روی","imNode",inboundNodes.map(n=>[n.id,n.name]),x?.node_id||inboundNodes[0]?.id)}${imS("پروتکل","imProtocol",protocols,p)}${imF("آدرس Listen","imListen",x?.listen||"0.0.0.0")}${imS("راهبرد آدرس اشتراک‌گذاری","imShare",[["node","آدرس نود"],["custom","آدرس سفارشی"],["request","دامنه درخواست"]],s.share_address_strategy||"node")}${imF("Public Host","imHostPublic",x?.public_host||"")}${imF("ترتیب در اشتراک","imSort",x?.sub_sort_index||1,"number")}${imF("پورت","imPort",x?.port||protocolPorts[p],"number")}${imF("ترافیک کل (GB)","imTraffic",s.total_traffic_gb||0,"number")}${imS("بازنشانی ترافیک","imReset",[["never","هرگز"],["daily","روزانه"],["weekly","هفتگی"],["monthly","ماهانه"]],s.traffic_reset||"never")}${imF("مدت زمان / انقضا","imExpiry",s.expiry_time||"","datetime-local")}${imF("توضیحات","imRemark",x?.remark||"")}</div></section>
 <section class="im-pane" data-pane="protocol"><div class="im-grid">${imProtocol(p,s)}</div></section><section class="im-pane" data-pane="transport">${p==="xray"?imTransport(s):'<div class="im-empty">تنظیمات انتقال این پروتکل در تب پروتکل است.</div>'}</section><section class="im-pane" data-pane="security">${p==="xray"?imSecurity(s):'<div class="im-empty">امنیت توسط سرویس اصلی و گواهی نود مدیریت می‌شود.</div>'}</section><section class="im-pane" data-pane="sniffing">${p==="xray"?imSniff(s):'<div class="im-empty">شنود فقط برای Xray کاربرد دارد.</div>'}</section><section class="im-pane" data-pane="advanced"><div class="im-empty">JSON اختیاری است؛ فیلدهای تب‌های قبلی مسیر اصلی ویرایش هستند.</div><div class="im-json-tabs"><button type="button" class="active">همه</button><button type="button">Settings</button><button type="button">Stream</button><button type="button">Sniffing</button></div><textarea id="imJson" class="im-code">${esc(JSON.stringify(s,null,2))}</textarea></section></main><footer><button type="button" id="imCancel">بستن</button><button class="primary">${x?"ذخیره تغییرات":"ایجاد"}</button></footer></form>`;document.body.appendChild(m);
 const close=()=>m.remove();imEl("imClose").onclick=imEl("imCancel").onclick=close;m.onclick=e=>{if(e.target===m)close()};m.querySelectorAll(".im-tabs button").forEach(b=>b.onclick=()=>{m.querySelectorAll(".im-tabs button,.im-pane").forEach(y=>y.classList.remove("active"));b.classList.add("active");m.querySelector('[data-pane="'+b.dataset.tab+'"]').classList.add("active")});m.querySelectorAll("[data-sec]").forEach(b=>b.onclick=()=>{m.querySelectorAll("[data-sec]").forEach(y=>y.classList.remove("active"));b.classList.add("active")});
 if(x){imEl("imNode").disabled=true;imEl("imProtocol").disabled=true}else imEl("imProtocol").onchange=()=>{const np=imV("imProtocol");close();openInboundModal(null,np)};
 imEl("imKeys")?.addEventListener("click",()=>{const a=new Uint8Array(32);crypto.getRandomValues(a);imEl("imPrivateKey").value=btoa(String.fromCharCode(...a)).replace(/[+/=]/g,"");imEl("imPublicKey").value="تولید توسط Agent هنگام اعمال"});imEl("imClearKeys")?.addEventListener("click",()=>{imEl("imPrivateKey").value="";imEl("imPublicKey").value=""});
 imEl("imForm").onsubmit=async e=>{e.preventDefault();try{const st=imCollect(p,s);st.share_address_strategy=imV("imShare");st.total_traffic_gb=+imV("imTraffic");st.traffic_reset=imV("imReset");st.expiry_time=imV("imExpiry")||null;const body={name:imV("imName"),listen:imV("imListen"),port:+imV("imPort"),public_host:imV("imHostPublic")||null,enabled:imC("imEnabled"),remark:imV("imRemark")||null,sub_sort_index:+imV("imSort"),settings:st};if(!x){body.node_id=imV("imNode");body.protocol=p}await req("/admin/inbounds"+(x?"/"+x.id:""),{method:x?"PATCH":"POST",body:JSON.stringify(body)});close();inbounds()}catch(err){showError(err)}};
};
window.editInbound=id=>openInboundModal(id);
window.inboundRuntime=async id=>{const x=await req("/admin/inbounds/"+id+"/runtime"),r=x.runtime||{},j=r.last_job,n=r.node||{};const body=serviceModal("وضعیت Inbound — "+x.name);body.innerHTML=
'<div class="runtime-grid"><div><small>وضعیت Inbound</small><b class="status '+esc(x.status)+'">'+esc(x.status)+'</b></div>'+
'<div><small>Node Agent</small><b>'+esc(n.status||"-")+' · '+esc(n.agent_version||"-")+'</b></div>'+
'<div><small>کلاینت‌های فعال</small><b>'+(r.clients_enabled||0)+' / '+(r.clients_total||0)+'</b></div>'+
'<div><small>ترافیک ثبت‌شده</small><b>'+((r.used_bytes||0)/1073741824).toFixed(2)+' GB</b></div>'+
'<div><small>Engine</small><b>'+esc(r.capabilities?.engine||"-")+'</b></div>'+
'<div><small>آخرین مشاهده Agent</small><b>'+(n.last_seen_at?new Date(n.last_seen_at).toLocaleString("fa-IR"):"-")+'</b></div></div>'+
'<h3>آخرین عملیات Agent</h3><div class="job-box"><b>'+esc(j?.type||"بدون عملیات")+'</b><span class="status '+esc(j?.status||"pending")+'">'+esc(j?.status||"-")+'</span><pre>'+esc(JSON.stringify(j?.result||{},null,2))+'</pre></div>'+
'<h3>قابلیت‌های فعال</h3><div class="feature-list">'+(r.capabilities?.features||[]).map(v=>'<span>'+esc(v)+'</span>').join("")+'</div>';};
inbounds=async function(){const [r,n,c]=await Promise.all([req("/admin/inbounds"),req("/nodes"),req("/admin/protocol-capabilities")]);inboundsData=r;inboundNodes=n;view.innerHTML=`<div class="panel"><div class="panel-heading"><div><h2>Inboundها</h2><p>ساخت و ویرایش کامل شنونده‌های VPN با فرم‌های استاندارد هر پروتکل</p></div><button class="primary" data-action="openInboundModal">＋ افزودن ورودی</button></div><div class="protocol-summary"><span>VLESS · VMess · Trojan · Shadowsocks</span><span>RAW · mKCP · WebSocket · gRPC · HTTPUpgrade · XHTTP</span><span>TLS · Reality</span><span>مرجع: ${esc(c.reference.name)} ${esc(c.reference.version)}</span></div><div class="table-wrap"><table><thead><tr><th>فعال</th><th>نام</th><th>پروتکل</th><th>انتقال</th><th>امنیت</th><th>Node</th><th>Listen / Port</th><th>Client</th><th>Job</th><th>وضعیت</th><th>عملیات</th></tr></thead><tbody>${r.map(x=>`<tr><td><span class="status ${x.enabled?"active":"revoked"}">${x.enabled?"فعال":"خاموش"}</span></td><td><b>${esc(x.name)}</b><small class="row-note">${esc(x.remark||"")}</small></td><td><span class="tag">${esc(x.settings?.variant||x.protocol)}</span></td><td>${esc(x.settings?.transport||"-")}</td><td>${esc(x.settings?.security||"-")}</td><td>${esc(n.find(v=>v.id===x.node_id)?.name||x.node_id)}</td><td dir="ltr">${esc(x.listen)}:${esc(x.port)}</td><td><span class="status ${esc(x.status)}">${esc(x.status)}</span></td><td class="row-actions"><button data-action="inboundRuntime" data-id="${x.id}">وضعیت</button><button data-action="editInbound" data-id="${x.id}">ویرایش</button><button class="danger" data-action="deleteInbound" data-id="${x.id}">حذف</button></td></tr>`).join("")}</tbody></table></div></div>`;bindActions()};


views.inbounds=inbounds;
