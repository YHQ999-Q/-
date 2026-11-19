/* 把教学 Agent 挂进她站点里那个空的 /teaching-agent 路由
   —— 不改她的 bundle，只在运行时把 iframe 塞进那个空 div。
   卸载/切路由后会自动重新挂上。 */
(function () {
  var SRC = 'http://127.0.0.1:5000/teach?v=' + Date.now();  // 对话页（带「回看」侧栏）
  var MOUNTED = false;

  function mount() {
    var el = document.querySelector('.teaching-agent-page');
    if (!el) { return false; }
    if (el.getAttribute('data-teach-mounted') === '1') { return true; }
    el.setAttribute('data-teach-mounted', '1');

    var f = document.createElement('iframe');
    f.src = SRC;
    f.title = '教学 Agent';
    // 关键：iframe 不声明这两项，里面的 getUserMedia 会被浏览器直接拒
    f.setAttribute('allow', 'microphone; camera; autoplay; clipboard-write');
    f.style.cssText =
      'width:100%;height:calc(100dvh - 185px);border:0;border-radius:20px;display:block;background:#f7f7f2';
    el.innerHTML = '';
    el.appendChild(f);
    MOUNTED = true;
    return true;
  }

  try {
    new MutationObserver(mount).observe(document.documentElement, {
      childList: true, subtree: true
    });
  } catch (e) {}
  setInterval(mount, 600);
  window.addEventListener('popstate', function () { setTimeout(mount, 50); });
  if (document.readyState !== 'loading') { mount(); }
  else { document.addEventListener('DOMContentLoaded', mount); }
})();
