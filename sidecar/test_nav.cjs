const { JSDOM } = require('/opt/masiha-vpn-build-20261011/src/frontend/node_modules/jsdom');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const code = fs.readFileSync(require('node:path').join(__dirname,'nav.js'),'utf8');
const pause = ms => new Promise(resolve => setTimeout(resolve,ms));
(async () => {
  // The injected script loads on login even before the SPA is mounted.
  const dom = new JSDOM('<!DOCTYPE html><html><head></head><body><main id="app">Login form</main></body></html>', {
    url:'https://exon.chanelchat.ir/xui/',runScripts:'dangerously',pretendToBeVisual:true
  });
  const win = dom.window;
  win.eval(code);
  await pause(40);
  assert.equal(win.document.querySelectorAll('.masiha-sidecar-menu-item').length,0,'Do not render an unauthenticated menu');

  // A subsequent SPA navigation should insert the top-level sidebar entry.
  const sidebar = win.document.createElement('div');
  sidebar.className='ant-sidebar';
  sidebar.innerHTML='<aside class="ant-layout-sider"><ul class="ant-menu sider-nav"><li class="ant-menu-item">Dashboard</li><li class="ant-menu-item">Inbounds</li><li class="ant-menu-item">Clients</li><li class="ant-menu-item">Groups</li></ul></aside>';
  win.document.body.append(sidebar);
  await pause(60);
  let links=win.document.querySelectorAll('.ant-sidebar .masiha-sidecar-menu-item a');
  assert.equal(links.length,4,'All four menu destinations');
  assert.deepEqual([...links].map(a=>a.hash),['#bot','#openvpn','#openconnect','#backup']);
  assert.match(win.document.querySelector('.masiha-sidecar-summary').textContent,/مدیریت مسیحا/);
  assert.equal(win.document.querySelectorAll('.ant-sidebar .masiha-sidecar-menu-item').length,1,'Exactly one desktop menu item');
  assert.equal(win.document.querySelector('.ant-sidebar .sider-nav').children[3].dataset.masihaMenu,'desktop','Manager follows native Clients item');
  win.document.querySelector('.ant-sidebar .masiha-sidecar-summary').click();
  assert.equal(win.document.querySelector('.ant-sidebar details').open,true,'Submenu opens');

  // React-style rerender replaces the UL. Observer reattaches a single menu.
  const newMenu=win.document.createElement('ul');
  newMenu.className='sider-nav ant-menu';
  newMenu.innerHTML='<li>New native nav</li>';
  sidebar.querySelector('ul').replaceWith(newMenu);
  await pause(60);
  assert.equal(win.document.querySelectorAll('.ant-sidebar .masiha-sidecar-menu-item').length,1,'Restored after native sidebar rerender');

  // Drawer exists outside desktop tree, mobile menu stays available.
  const drawer=win.document.createElement('div');
  drawer.className='ant-drawer';
  drawer.innerHTML='<ul class="drawer-nav ant-menu"></ul>';
  win.document.body.append(drawer);
  await pause(60);
  assert.equal(win.document.querySelectorAll('.ant-drawer .masiha-sidecar-menu-item').length,1,'Mobile drawer item available');
  assert.equal(win.document.querySelectorAll('.masiha-sidecar-menu-item').length,2,'No duplicate menu item after sync');

  // Old native Ant Design sales menu must not show the incomplete screen.
  const legacy=win.document.createElement('li');
  legacy.className='ant-menu-item';
  legacy.setAttribute('data-menu-id','/sales-bot');
  legacy.textContent='تنظیمات ربات فروش';
  win.document.querySelector('.ant-sidebar ul').appendChild(legacy);
  await pause(60);
  assert.equal(legacy.hidden,true,'Old incomplete sales link is hidden');

  // Official updates can change classes: on panel path a fallback remains.
  sidebar.remove(); drawer.remove();
  win.history.replaceState({},'','/xui/panel/');
  win.dispatchEvent(new win.PopStateEvent('popstate'));
  await pause(60);
  assert.equal(win.sessionStorage.getItem('masiha:native-panel'),'/xui/','Native panel path cached after SPA navigation');
  assert.equal(win.document.querySelectorAll('#masiha-nav-fallback').length,1,'Fallback route without matched sidebar');
  assert.equal(win.document.querySelector('#masiha-nav-fallback a').getAttribute('href'),'/masiha-ui/');

  // If the sidebar returns, floating fallback disappears.
  win.document.body.append(sidebar);
  await pause(60);
  assert.equal(win.document.querySelector('#masiha-nav-fallback'),null,'Fallback hidden when native menu visible');
  console.log('MENU_TEST_PASS: login, SPA, sidebar, submenu, re-render, mobile drawer, fallback');
  process.exit(0);
})().catch(err=>{console.error(err);process.exit(1)});
