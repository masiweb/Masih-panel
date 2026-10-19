/*
 * Masiha navigation bridge. This file is served by Nginx, not embedded in
 * SANAei's x-ui executable. It survives normal native x-ui upgrades.
 *
 * Never reads session cookies, tokens, or local storage. Routes use only
 * same-origin URLs. A MutationObserver handles delayed SPA menus and drawers.
 */
(function () {
  'use strict';
  if (window.__masihaNavBridgeStarted) return;
  window.__masihaNavBridgeStarted = true;

  var PREFIX = '/masiha-ui/';
  var ITEMS = [
    { key: 'bot', text: 'تنظیمات ربات فروش', icon: '🤖' },
    { key: 'openvpn', text: 'OpenVPN', icon: '🔒' },
    { key: 'openconnect', text: 'OpenConnect', icon: '🌐' },
    { key: 'backup', text: 'بکاپ و بازیابی', icon: '💾' }
  ];

  // The official panel can render under /xui/panel or an SPA route using
  // /xui/. Deliberately do not return early from the login path.
  var css = document.createElement('link');
  css.rel = 'stylesheet';
  css.href = PREFIX + 'nav.css';
  css.id = 'masiha-navigation-style';
  document.head.appendChild(css);

  function createLink(item) {
    var link = document.createElement('a');
    link.className = 'masiha-nav-child';
    link.href = PREFIX + '#' + item.key;
    link.setAttribute('aria-label', item.text);
    var icon = document.createElement('span');
    icon.className = 'masiha-nav-child-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = item.icon;
    var label = document.createElement('span');
    label.textContent = item.text;
    link.appendChild(icon);
    link.appendChild(label);
    return link;
  }

  function createNav(kind) {
    var root = document.createElement('li');
    root.className = 'masiha-sidecar-menu-item';
    root.dataset.masihaMenu = kind;
    root.setAttribute('role', 'none');

    var details = document.createElement('details');
    details.className = 'masiha-sidecar-details';
    var summary = document.createElement('summary');
    summary.setAttribute('aria-label', 'مدیریت مسیحا');
    summary.setAttribute('title', 'مدیریت مسیحا');
    summary.className = 'masiha-sidecar-summary';

    var icon = document.createElement('span');
    icon.className = 'masiha-sidecar-main-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = '⚙';
    var text = document.createElement('span');
    text.className = 'masiha-sidecar-label';
    text.textContent = 'مدیریت مسیحا';
    var caret = document.createElement('span');
    caret.className = 'masiha-sidecar-chevron';
    caret.setAttribute('aria-hidden', 'true');
    caret.textContent = '⌄';
    summary.append(icon, text, caret);
    details.appendChild(summary);

    var links = document.createElement('div');
    links.className = 'masiha-sidecar-submenu';
    links.setAttribute('aria-label', 'بخش‌های مدیریت مسیحا');
    ITEMS.forEach(function (item) { links.appendChild(createLink(item)); });
    details.appendChild(links);
    root.appendChild(details);

    summary.addEventListener('click', function () {
      // When the rail is collapsed, link list is still accessible using
      // keyboard focus/click and overlays outside the sidebar's narrow rail.
      root.classList.add('masiha-user-touched');
    });
    // Never let Ant Design's delegated navigation handler interpret the
    // external module links as internal SPA menu selections.
    root.addEventListener('click', function (event) { event.stopPropagation(); });

    return root;
  }

  function isElementVisible(element) {
    return !!element && element.isConnected;
  }

  function desktopMenu() {
    var sidebar = document.querySelector('.ant-sidebar');
    var selector = '.sider-nav, ul.ant-menu, [role="menu"]';
    if (sidebar) {
      var el = sidebar.querySelector(selector);
      if (el && el.tagName === 'UL') return el;
    }
    var aside = document.querySelector('aside.ant-layout-sider, .ant-layout-sider');
    if (aside) {
      var menu = aside.querySelector(selector);
      if (menu && menu.tagName === 'UL') return menu;
    }
    return null;
  }

  function mobileMenus() {
    // Drawer is portalled outside the desktop .ant-sidebar tree.
    var nodes = document.querySelectorAll('.ant-drawer .drawer-nav, .ant-drawer ul.ant-menu');
    var result = [];
    nodes.forEach(function (node) {
      if (node.tagName === 'UL' && result.indexOf(node) < 0) result.push(node);
    });
    return result;
  }

  function ensureItem(menu, kind) {
    if (!isElementVisible(menu)) return false;
    var matches = menu.querySelectorAll(':scope > .masiha-sidecar-menu-item');
    if (matches.length > 1) {
      for (var i = 1; i < matches.length; i++) matches[i].remove();
    }
    if (matches.length === 0) {
      // Place the section next to native Dashboard/Inbounds/Clients rather
      // than after all native routes, where it could be below the fold.
      var nativeItems = Array.prototype.filter.call(menu.children, function (el) {
        return el.matches('.ant-menu-item, .ant-menu-submenu');
      });
      menu.insertBefore(createNav(kind), nativeItems[3] || null);
      return true;
    }
    return false;
  }

  function fallback() {
    var existing = document.getElementById('masiha-nav-fallback');
    // Only show a fallback on an authenticated-looking panel route.
    // No unauthenticated navigation widget on the x-ui login screen.
    var eligible = /^\/(?:[A-Za-z0-9._~-]+\/){1,6}panel(?:\/|$)/.test(location.pathname);
    if (!eligible) {
      if (existing) existing.remove();
      return;
    }
    if (existing) return;
    var root = document.createElement('nav');
    root.id = 'masiha-nav-fallback';
    root.dir = 'rtl';
    root.setAttribute('aria-label', 'مدیریت مسیحا');
    var anchor = document.createElement('a');
    anchor.href = PREFIX;
    anchor.textContent = '⚙ مدیریت مسیحا';
    root.appendChild(anchor);
    document.body.appendChild(root);
  }

  var scheduled = false;
  function synchronize() {
    scheduled = false;
    if (!document.body) return;
    var nav = desktopMenu();
    var found = false;
    if (nav) {
      ensureItem(nav, 'desktop');
      found = true;
    }
    mobileMenus().forEach(function (m) {
      ensureItem(m, 'mobile');
      found = true;
    });
    var fallbackNode = document.getElementById('masiha-nav-fallback');
    if (found) {
      if (fallbackNode) fallbackNode.remove();
    } else {
      fallback();
    }
  }
  function requestSync() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(synchronize);
  }

  if (document.body) requestSync();
  else document.addEventListener('DOMContentLoaded', requestSync, { once: true });

  var observer = new MutationObserver(requestSync);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  // Browser back/forward and SPA navigation can update route without a reload.
  window.addEventListener('popstate', requestSync);
  window.addEventListener('hashchange', requestSync);
  document.addEventListener('visibilitychange', requestSync);
})();
