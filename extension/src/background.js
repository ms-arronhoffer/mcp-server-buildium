/**
 * Background service worker / script.
 *
 * Chrome: open the side panel when the toolbar action is clicked.
 * Firefox: the `sidebar_action` toolbar button opens the sidebar natively, so no
 * action wiring is required here.
 */

import { getApi } from "./browser.js";

const api = getApi();

// Chrome: clicking the action icon opens the side panel.
if (api.sidePanel && api.sidePanel.setPanelBehavior) {
  api.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.error("Failed to set side panel behavior:", err));
}
