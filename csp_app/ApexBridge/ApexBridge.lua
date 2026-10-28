-- CSP app API: extension/internal/lua-sdk/ac_apps/lib.lua (installed SDK).
-- No sockets, file writes or physics mutation. Python relays local JSON only.
local SNAPSHOT_KEY = '.apex.bridge.snapshot'
local REQUEST_KEY = '.apex.pit.request'
local MAX_CARS = 64
local CARS_PER_STEP = 2
local STEP_SECONDS = 0.05
local PUBLISH_SECONDS = 0.5
local STALE_SECONDS = 3.0
-- Verified in AC 1.16.4/CSP 0.3.0-preview581: No change=-1, S=0,
-- M=1, H=2 on the Kunos Porsche 911 GT3 R 2016. Also require the
-- same complete numeric range for every car; other mappings stay unavailable.
local VERIFIED_PIT_COMPOUND_OFFSET = 0

local state = {sequence = 0, nonce = '', context = '', cars = {}, metadata = {}, pendingAck = nil,
  cursor = 0, step = 0, publish = 0, ack = nil, seen = {}, seenOrder = {}, status = 'Waiting for a session'}

local function number(v, lower, upper)
  if type(v) ~= 'number' or v ~= v or v == math.huge or v == -math.huge then return nil end
  if lower and v < lower or upper and v > upper then return nil end
  return v
end

local function integer(v, lower, upper)
  v = number(v, lower, upper)
  return v and v == math.floor(v) and v or nil
end

local function text(v)
  return type(v) == 'string' and #v <= 200 and v or nil
end

local function call(name, ...)
  local fn, args = ac[name], {...}
  if fn == nil then return nil end
  local ok, value = pcall(function() return fn(unpack(args)) end)
  return ok and value or nil
end

local function boolean(value)
  if type(value) == 'boolean' then return value end
  return nil
end

local function reset()
  local sim = ac.getSim()
  state.nonce = tostring(os.time()) .. ':' .. tostring(sim.time) .. ':' .. tostring(state.sequence)
  state.sequence = state.sequence + 1
  state.cars, state.metadata, state.seen, state.seenOrder = {}, {}, {}, {}
  state.cursor, state.ack, state.pendingAck = 0, nil, nil
end

local function context(sim)
  local car, track, layout = text(call('getCarID', 0)), text(call('getTrackID')), text(call('getTrackLayout')) or ''
  local index, kind = integer(sim.currentSessionIndex, 0), integer(sim.raceSessionType, 0)
  local key = tostring(car) .. '|' .. tostring(track) .. '|' .. layout .. '|' .. tostring(index) .. '|' .. tostring(kind)
  if key ~= state.context then
    state.context = key
    reset()
  end
  return {car_id = car, track_id = track, layout = layout, session_index = index,
    session_type = kind, session_nonce = state.nonce}
end

local function carRow(id, sim)
  local car = ac.getCar(id)
  if car == nil or not car.isConnected then return nil end
  local now = sim.gameTime
  local meta = state.metadata[id]
  if not meta or now - meta.at > 10 or now < meta.at then
    meta = {at = now, car = text(call('getCarID', id)), name = text(call('getDriverName', id))}
    state.metadata[id] = meta
  end
  local row = {id = id, car = meta.car, name = meta.name,
    lap_count = integer(car.lapCount, 0), lap_time_ms = integer(car.lapTimeMs, 0),
    last_lap_ms = integer(car.previousLapTimeMs, 0), race_position = integer(car.racePosition, 1),
    normalized_pos = number(car.splinePosition, 0, 1), speed_kmh = number(car.speedKmh, 0, 1000),
    tyre_compound = text(call('getTyresLongName', id)), compound_index = integer(car.compoundIndex, 0, 31),
    last_lap_valid = boolean(car.isLastLapValid), is_ai_controlled = boolean(car.isAIControlled),
    in_pit = boolean(car.isInPitlane or car.isInPit), best_lap_from_ghost = boolean(car.bestLapFromGhost)}
  if not car.bestLapFromGhost then row.best_lap_ms = integer(car.bestLapTimeMs, 1) end
  -- CSP Race is 3. In practice this API returns a best-lap difference, not a
  -- physical gap, so deliberately leave the gap unavailable outside races.
  if sim.raceSessionType == ac.SessionType.Race and id ~= 0 then
    row.gap_to_player_s = number(call('getGapBetweenCars', 0, id))
    row.gap_source = 'CSP distance/speed estimate; positive means car ahead of player'
  end
  return row
end

local function compounds()
  local result, seen = {}, {}
  for index = 0, 31 do
    local short = text(call('getTyresName', 0, index))
    local name = text(call('getTyresLongName', 0, index)) or short
    if not name or name == '' then break end
    local key = tostring(short) .. '|' .. name
    if seen[key] then break end
    seen[key] = true
    result[#result + 1] = {index = index, name = name, short_name = short,
      pit_value = VERIFIED_PIT_COMPOUND_OFFSET and index + VERIFIED_PIT_COMPOUND_OFFSET or nil}
  end
  return result
end

local function pitstop(sim, available)
  local spinners = call('getPitstopSpinners')
  if type(spinners) == 'table' then
    for _, spinner in ipairs(spinners) do
      if spinner.type == 'compound' then
        local verified = VERIFIED_PIT_COMPOUND_OFFSET ~= nil and type(available) == 'table'
          and #available > 0 and spinner.min == -1 and spinner.max == #available - 1
        return {available = verified and spinner.readOnly == false,
          name = text(spinner.name), min = integer(spinner.min), max = integer(spinner.max),
          value = integer(spinner.value), read_only = boolean(spinner.readOnly),
          preset = integer(sim.currentQuickPitPreset, 0, 2), mapping_verified = verified,
          message = not verified and 'Quick Pit compound mapping does not match the verified range; select tyres manually in the game.' or nil}
      end
    end
  end
  return {available = false, mapping_verified = false, message = 'Quick Pit compound control is unavailable in this CSP session.'}
end

local function ack(command, ok, message, actual)
  state.ack = {id = command.id, ok = ok, requested_index = command.index, actual_value = actual, message = message}
end

local function pitRequest(ctx, sim, available, spinner)
  if state.pendingAck then
    local pending = state.pendingAck
    if spinner.name == pending.name and spinner.preset == pending.preset and spinner.value == pending.value then
      ack(pending.command, true, 'Quick Pit compound selected; tyres will change only during the game pit service.', spinner.value)
      state.pendingAck = nil
    elseif sim.gameTime - pending.at > 1 then
      ack(pending.command, false, 'The game did not confirm the Quick Pit compound selection.', spinner.value)
      state.pendingAck = nil
    end
  end
  local raw = call('load', REQUEST_KEY)
  if type(raw) ~= 'string' or #raw == 0 or #raw > 4096 then return end
  local ok, command = pcall(JSON.parse, raw)
  if not ok or type(command) ~= 'table' or type(command.id) ~= 'string' or #command.id > 100 or #command.id == 0 then return end
  if state.seen[command.id] then return end
  state.seen[command.id] = true
  state.seenOrder[#state.seenOrder + 1] = command.id
  if #state.seenOrder > 32 then state.seen[table.remove(state.seenOrder, 1)] = nil end
  if command.car_id ~= ctx.car_id or command.track_id ~= ctx.track_id or command.layout ~= ctx.layout
      or command.session_nonce ~= ctx.session_nonce then
    ack(command, false, 'Session changed; tyre request was not applied.')
    return
  end
  local remaining = number(command.expires_at) and command.expires_at - os.time() or nil
  if not remaining or remaining <= 0 or remaining > 15 or not sim.isLive or sim.isReplayActive then
    ack(command, false, 'Tyre request expired or the live session is unavailable.')
    return
  end
  local index, selected = integer(command.index, 0, 31), nil
  for _, item in ipairs(available) do if item.index == index then selected = item; break end end
  if not selected or type(command.compound) ~= 'string' or
      (command.compound ~= selected.name and command.compound ~= selected.short_name) then
    ack(command, false, 'Requested compound does not match this car.')
    return
  end
  if not spinner.available or not spinner.mapping_verified or selected.pit_value == nil then
    ack(command, false, spinner.message or 'Quick Pit mapping is unavailable; choose tyres manually.')
    return
  end
  if not spinner.name or not spinner.preset or spinner.min == nil or spinner.max == nil or
      selected.pit_value < spinner.min or selected.pit_value > spinner.max then
    ack(command, false, 'The Quick Pit selection cannot be edited safely.')
    return
  end
  local accepted = call('setPitstopSpinnerValue', spinner.name, selected.pit_value, spinner.preset)
  local after = pitstop(sim, available)
  if accepted == true and after.value ~= selected.pit_value then
    state.pendingAck = {command = command, name = spinner.name, preset = spinner.preset,
      value = selected.pit_value, at = sim.gameTime}
  else
    ack(command, accepted == true and after.value == selected.pit_value,
      accepted == true and after.value == selected.pit_value and 'Quick Pit compound selected; tyres will change only during the game pit service.'
        or 'The game did not confirm the Quick Pit compound selection.', after.value)
  end
end

local function publish(sim, ctx)
  local rows = {}
  for id = 0, math.min(MAX_CARS, sim.carsCount) - 1 do
    local sample = state.cars[id]
    if sample and sim.gameTime - sample.at >= 0 and sim.gameTime - sample.at <= STALE_SECONDS then
      rows[#rows + 1] = sample.row
    end
  end
  local player = ac.getCar(0)
  local groundtruth = {is_ai_controlled = boolean(player.isAIControlled),
    in_pit = boolean(player.isInPitlane or player.isInPit), speed_kmh = number(player.speedKmh, 0, 1000),
    lap_count = integer(player.lapCount, 0), normalized_pos = number(player.splinePosition, 0, 1)}
  if player.physicsAvailable then groundtruth.lap_valid = boolean(player.isLapValid) end
  local available = compounds()
  local spinner = pitstop(sim, available)
  pitRequest(ctx, sim, available, spinner)
  state.sequence = state.sequence + 1
  ctx.sequence, ctx.cars, ctx.player, ctx.compounds, ctx.pitstop, ctx.ack = state.sequence, rows, groundtruth, available, spinner, state.ack
  ctx.schema, ctx.timestamp, ctx.gap_is_estimate = 1, sim.systemTime, true
  call('store', SNAPSHOT_KEY, JSON.stringify(ctx))
  state.status = 'Local session bridge active. ' .. tostring(#rows) .. ' cars sampled.'
end

function script.update(dt)
  local sim = ac.getSim()
  local ctx = context(sim)
  state.step, state.publish = state.step + math.max(0, dt), state.publish + math.max(0, dt)
  if state.step >= STEP_SECONDS then
    state.step = 0 -- no catch-up burst after a slow frame
    local count = math.min(MAX_CARS, sim.carsCount)
    for _ = 1, math.min(count, CARS_PER_STEP) do
      local id = state.cursor % count
      state.cursor = (id + 1) % count
      local ok, row = pcall(carRow, id, sim)
      state.cars[id] = ok and row and {at = sim.gameTime, row = row} or nil
    end
  end
  if state.publish >= PUBLISH_SECONDS then
    state.publish = 0
    publish(sim, ctx)
  end
end

function script.windowMain()
  ui.textWrapped(state.status)
  ui.separator()
  ui.textWrapped('APEX receives timing and definitive player lap validity locally. Quick Pit changes require a verified compound mapping and game acknowledgement.')
end

local sessionCallback = call('onSessionStart', function() reset() end)
reset()
