const currentYear = String(new Date().getFullYear());
const scriptUrl = new URL(document.currentScript.src, window.location.href);
const beianIconUrl = new URL("./beian-icon.png", scriptUrl).href;

document.querySelectorAll("[data-site-footer]").forEach((node) => {
  node.innerHTML = `
    <div>© <span>${currentYear}</span> Anfine Station</div>
    <div class="beian">
      <span class="beian-item">皖ICP备2026007591号-1</span>
      <span class="beian-item beian-police">
        <img src="${beianIconUrl}" alt="备案图标" class="beian-icon">
        <span>皖公网安备34050302000948号</span>
      </span>
    </div>
  `;
});
