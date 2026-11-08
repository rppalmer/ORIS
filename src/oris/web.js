// The site's navigation, kept here so every page shows the same links.
(() => {
  const PAGES = [
    ["Chat", "/"],
    ["Tools", "/tools"],
    ["Status", "/status"],
    ["Runs", "/runs"],
  ];
  const path = location.pathname;
  const inChat = path.startsWith("/chat/");
  const current = inChat ? "/" : `/${path.split("/")[1]}`;

  // Remembered per tab, because a conversation has no other way to be found
  // again once you have moved to another page.
  if (inChat) sessionStorage.setItem("oris-chat", path);

  const nav = document.querySelector("nav");
  for (const [label, href] of PAGES) {
    if (href === current) {
      const here = document.createElement("span");
      here.textContent = label;
      here.setAttribute("aria-current", "page");
      nav.append(here);
      continue;
    }
    const link = document.createElement("a");
    link.textContent = label;
    link.target = "_self";
    link.href = href === "/" ? sessionStorage.getItem("oris-chat") ?? "/" : href;
    nav.append(link);
  }
})();
