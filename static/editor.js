document.querySelectorAll("[data-rich-editor]").forEach((form) => {
  const textarea = form.querySelector("textarea[name='content']");
  const surface = form.querySelector("[data-editor-surface]");
  const preview = form.querySelector("[data-editor-preview]");

  const sync = () => {
    textarea.value = surface.innerHTML.trim();
    preview.innerHTML = textarea.value;
  };

  sync();
  surface.addEventListener("input", sync);

  form.querySelectorAll("[data-editor-command]").forEach((button) => {
    button.addEventListener("click", () => {
      const command = button.dataset.editorCommand;
      surface.focus();
      if (command === "h2") {
        document.execCommand("formatBlock", false, "h2");
      } else if (command === "bold") {
        document.execCommand("bold");
      } else if (command === "italic") {
        document.execCommand("italic");
      } else if (command === "blockquote") {
        document.execCommand("formatBlock", false, "blockquote");
      } else if (command === "ul") {
        document.execCommand("insertUnorderedList");
      } else if (command === "link") {
        const url = window.prompt("Enter URL");
        if (url) {
          document.execCommand("createLink", false, url);
        }
      }
      sync();
    });
  });

  form.addEventListener("submit", sync);
});
