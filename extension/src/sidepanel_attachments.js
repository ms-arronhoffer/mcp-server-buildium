import { MAX_ATTACHMENT_BYTES, fileToAttachment, validateFile } from "./attachments.js";

export class AttachmentController {
  /**
   * @param {HTMLElement} attachmentsEl
   * @param {(msg:string,isError?:boolean)=>void} showBanner
   */
  constructor(attachmentsEl, showBanner) {
    this.attachmentsEl = attachmentsEl;
    this.showBanner = showBanner;
    /** @type {Array<{name:string, media_type:string, data:string}>} */
    this.pending = [];
  }

  render() {
    this.attachmentsEl.textContent = "";
    this.attachmentsEl.classList.toggle("hidden", this.pending.length === 0);
    this.pending.forEach((att, index) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      const label = document.createElement("span");
      label.className = "chip-label";
      label.textContent = att.name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "chip-remove";
      remove.title = "Remove attachment";
      remove.textContent = "✕";
      remove.addEventListener("click", () => {
        this.pending.splice(index, 1);
        this.render();
      });
      chip.append(label, remove);
      this.attachmentsEl.appendChild(chip);
    });
  }

  async addFiles(fileList) {
    const files = Array.from(fileList || []);
    for (const file of files) {
      const error = validateFile(file, MAX_ATTACHMENT_BYTES);
      if (error) {
        this.showBanner(error, true);
        continue;
      }
      try {
        this.pending.push(await fileToAttachment(file));
      } catch {
        this.showBanner(`Could not read ${file.name}.`, true);
      }
    }
    this.render();
  }

  consume() {
    const attachments = this.pending;
    this.pending = [];
    this.render();
    return attachments;
  }
}
