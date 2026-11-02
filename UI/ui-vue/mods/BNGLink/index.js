import { lua } from "@/bridge"

const TAB_ID = "bnglink"
const BUTTON_ID = "bnglink-live-sync"

export async function onLoad() {
  await lua.extensions.load("BNGLink_BNGLink")
  await lua.extensions.ui_pause_actions.registerModTab({
    id: TAB_ID,
    label: "BNGLink",
    icon: "link",
    hideRail: true,
  })
  await lua.extensions.ui_pause_actions.registerModButton({
    id: BUTTON_ID,
    tabId: TAB_ID,
    label: "Live sync",
    icon: "link",
    componentName: "/ui/ui-vue/mods/BNGLink/BNGLink.vue",
  })
}

export async function onUnload() {
  await lua.extensions.ui_pause_actions.unregisterModButton(BUTTON_ID)
  await lua.extensions.ui_pause_actions.unregisterModTab(TAB_ID)
}
