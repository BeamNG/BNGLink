<template>
  <div ref="rootEl" class="bnglink">
    <div class="bnglink-content">
      <div class="bnglink-header">
        <BngCardHeading type="ribbon">BNGLink</BngCardHeading>
        <span class="bnglink-status" :class="{ connected: state.connected }">
          <BngIcon :type="state.connected ? icons.link : icons.unlink" />
          {{ state.connected ? `Connected to ${state.connectedTo}` : "Disconnected" }}
        </span>
      </div>

      <BngGroupPanel title="Connection settings">
        <!-- Not a BngRow: a row forwards activation to a single control -->
        <div v-for="endpoint in ENDPOINTS" :key="endpoint.label" class="bnglink-endpoint">
          <span>{{ endpoint.label }}</span>
          <BngInput
            v-if="endpoint.hostKey"
            :model-value="connection[endpoint.hostKey]"
            prefix="IP"
            :show-external-button="false"
            @update:model-value="setConnectionValue(endpoint.hostKey, $event)"
            @blur="applyConnectionSettings"
            @enter="applyConnectionSettings"
          />
          <BngInput
            class="bnglink-port"
            :model-value="connection[endpoint.portKey]"
            type="number"
            :min="1"
            :max="65535"
            no-spinners
            prefix="Port"
            :show-external-button="false"
            @update:model-value="setConnectionValue(endpoint.portKey, $event)"
            @blur="applyConnectionSettings"
            @enter="applyConnectionSettings"
          />
        </div>
      </BngGroupPanel>

      <BngGroupPanel title="Sync settings">
        <BngRow
          label="Smooth digital dashboards"
          tooltip="Enables 24FPS for digital dashboards. WARNING : This can significantly reduce the game's framerate."
        >
          <BngSwitch :model-value="state.smoothDashboards" @update:model-value="call('setSmoothDashboards', $event)" />
        </BngRow>
        <BngGroupPanel title="Vehicles">
          <div class="bnglink-vehicles">
            <BngRow
              v-for="vehicle in state.vehicles"
              :key="vehicle.id"
              :class="{ 'bnglink-driven': vehicle.driven }"
              :label="`${vehicle.id} - ${vehicle.name}`"
            >
              <BngSwitch
                :model-value="vehicle.enabled"
                @update:model-value="call('setVehicleSyncEnabled', vehicle.id, $event)"
              />
            </BngRow>
            <div v-if="!state.vehicles.length" class="bnglink-empty">No vehicles in the scene</div>
          </div>
        </BngGroupPanel>
      </BngGroupPanel>
    </div>

    <PauseTabFooter>
      <BngButton
        class="bnglink-action"
        :accent="state.liveSync ? ACCENTS.attention : ACCENTS.main"
        :icon="state.liveSync ? icons.square : icons.play"
        @click="call(state.liveSync ? 'stopRealtime' : 'startRealtime')"
      >
        {{ state.liveSync ? "Stop live sync" : "Start live sync" }}
      </BngButton>
      <BngButton
        class="bnglink-action"
        :accent="ACCENTS.secondary"
        :icon="icons.sync"
        :disabled="state.resyncing"
        @click="call('resync')"
      >
        {{ state.resyncing ? "Resyncing..." : "Resync link" }}
      </BngButton>
    </PauseTabFooter>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from "vue"
import {
  ACCENTS,
  BngButton,
  BngCardHeading,
  BngGroupPanel,
  BngIcon,
  BngInput,
  BngRow,
  BngSwitch,
  icons,
} from "@/common/components/base"
import PauseTabFooter from "@/modules/pause/components/PauseTabFooter.vue"
import { run as runLua } from "@/bridge/libs/Lua.js"

const LUA_BACKEND = "extensions.BNGLink_BNGLink"
const POLL_INTERVAL_MS = 500

// BeamNG listens on any interface, so only the receiver has an IP
const ENDPOINTS = [
  { label: "BeamNG", portKey: "beamngPort" },
  { label: "Receiver", hostKey: "receiverHost", portKey: "receiverPort" },
]

const rootEl = ref(null)

const state = reactive({
  connected: false,
  connectedTo: "",
  vehicles: [],
  liveSync: false,
  resyncing: false,
  smoothDashboards: false,
})

const connection = reactive({
  beamngPort: 0,
  receiverHost: "",
  receiverPort: 0,
})

let pollTimer
let pauseMainCard
// Set while the user edits the connection fields, so polling doesn't overwrite them
let connectionDirty = false

async function refreshState() {
  const next = await runLua(`${LUA_BACKEND}.getUiState`, [], {})
  if (!next) return
  const { connection: nextConnection, ...nextState } = next
  Object.assign(state, nextState)
  if (!connectionDirty) Object.assign(connection, nextConnection)
}

async function call(functionName, ...args) {
  await runLua(`${LUA_BACKEND}.${functionName}`, args, {})
  await refreshState()
}

function setConnectionValue(key, value) {
  connection[key] = value
  connectionDirty = true
}

// Invalid values are rejected by Lua and replaced by the active settings on refresh
function applyConnectionSettings() {
  if (!connectionDirty) return
  connectionDirty = false
  call("setConnectionSettings", connection)
}

onMounted(() => {
  // The pause card scrolls (overflow-y: auto, which also scrolls sideways) and mod tabs can't opt out through a card class
  pauseMainCard = rootEl.value?.closest(".pause-main-card")
  if (pauseMainCard) pauseMainCard.style.overflowY = "visible"
  refreshState()
  pollTimer = window.setInterval(refreshState, POLL_INTERVAL_MS)
})

onUnmounted(() => {
  window.clearInterval(pollTimer)
  if (pauseMainCard) pauseMainCard.style.overflowY = ""
})
</script>

<style scoped lang="scss">
.bnglink {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
  color: var(--bng-off-white);

  :deep(.pause-tab-footer) {
    padding: 0.75rem;
  }

  :deep(.pause-tab-footer-primary) {
    gap: 0.5rem;
  }
}

.bnglink-content {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  gap: 0.75em;
  min-height: 0;
  padding: 0.5em 0.75em;
  overflow-y: auto;
}

.bnglink-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1em;
}

.bnglink-status {
  display: inline-flex;
  align-items: center;
  gap: 0.35em;
  color: var(--bng-cool-gray-300);

  &.connected {
    color: var(--bng-add-green-400);
  }
}

.bnglink-endpoint {
  display: grid;
  grid-template-columns: 6em 1fr 9em;
  align-items: center;
  gap: 0.5em;
  margin: 0.25em;
  padding: 0.25em 0.25em 0.25em 0.5em;
}

.bnglink-port {
  grid-column: 3;
}

.bnglink-vehicles {
  display: flex;
  flex-direction: column;
  max-height: 20em;
  overflow-y: auto;
}

.bnglink-driven {
  --bng-bg-enabled: var(--bng-cool-gray-650);
  --bng-bg-enabled-opacity: 1;
}

.bnglink-empty {
  padding: 0.75em 0.5em;
  color: var(--bng-cool-gray-400);
}

.bnglink-action {
  flex: 1 1 0;
  --bng-button-margin: 0;
  --bng-button-max-width: 100%;
  --bng-button-padding-top: 0.75em;
  --bng-button-padding-bottom: 0.75em;
  font-size: 1.1em;
}
</style>
