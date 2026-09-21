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
  view.innerHTML=`<div class="panel"><h2>Inboundها</h2><div class="help-box"><b>Inbound</b> شنونده واقعی روی یک Node است. پروتکل، پورت، Transport، امنیت و تمام تنظیمات سرور از اینجا کنترل می‌شود.</div><form id="inboundForm"><div class="grid-form"><label class="field"><span>نام</span><input id="ibName" required placeholder="Germany-VLESS-Reality"></label><label class="field"><span>Node</span><select id="ibNode">${nodes.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select></label><label class="field"><span>پروتکل</span><select id="ibProtocol"><option value="xray">Xray</option><option value="wireguard">WireGuard</option><option value="openvpn">OpenVPN</option><option value="openconnect">OpenConnect</option></select></label><label class="field"><span>Listen</span><input id="ibListen" value="0.0.0.0"></label><label class="field"><span>Port</span><input id="ibPort" type="number" value="8443" min="1" max="65535"></label><label class="field"><span>Public Host</span><input id="ibPublicHost" placeholder="xu.chanelchat.ir"></label></div><h3>تنظیمات پروتکل</h3><div id="ibProtocolFields" class="grid-form">${inboundFields("xray")}</div><details><summary>تنظیمات پیشرفته JSON</summary><textarea id="ibAdvanced" class="config-box" placeholder='{"sockopt":{}}'></textarea></details><button class="primary">ساخت Inbound</button></form><div class="table-wrap"><table><thead><tr><th>نام</th><th>Node</th><th>پروتکل</th><th>Listen/Port</th><th>وضعیت</th><th>عملیات</th></tr></thead><tbody>${rows.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(nodes.find(n=>n.id===x.node_id)?.name||x.node_id)}</td><td>${esc(x.protocol)}</td><td dir="ltr">${esc(x.listen)}:${esc(x.port)}</td><td><span class="status ${esc(x.status)}">${esc(x.status)}</span></td><td><button data-action="editInbound" data-id="${x.id}">ویرایش کامل</button><button class="danger" data-action="deleteInbound" data-id="${x.id}">حذف</button></td></tr>`).join("")}</tbody></table></div></div>`;
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
