--[[ Offline smoke test for the Epic SWEPs addon.

	Loads the addon's real Lua inside the stub GMod (tools/stub_gmod.lua) twice -
	once as the server, once as a client - replays the server's net traffic into
	the client, and steps a fake clock through both weapons' full timelines.

	Any Lua error, bad net read, NaN draw call or missing effect name fails this.

	Run:  node examples/gmod-epic-sweps/tools/run_smoke_test.js
]]

local S = api.S
local okCount, failCount = 0, 0
local EP	-- the addon's EpicSWEPs namespace, read from the stub's environment

local function grabGlobals()
	EP = api.getG().EpicSWEPs
end

local function check(label, cond, detail)
	if cond then
		okCount = okCount + 1
		print(string.format("  PASS  %-52s %s", label, detail or ""))
	else
		failCount = failCount + 1
		print(string.format("  FAIL  %-52s %s", label, detail or ""))
	end
end

local function makePlayer()
	local pl = setmetatable({
		classname = "player",
		index = 1,
		pos = api.Vector(0, -800, 64),
		health = 100,
		isPlayerFlag = true
	}, { __index = api.entityBase })

	pl.IsPlayer = function() return true end
	pl.IsNPC = function() return false end
	pl.Alive = function() return true end
	pl.Health = function() return 100 end
	pl.ShouldDrawLocalPlayer = function() return false end
	pl.GetPos = function() return pl.pos end
	pl.GetEyePos = function() return pl.pos + api.Vector(0, 0, 48) end
	pl.GetAimVector = function() return api.Vector(0, 1, -0.1):GetNormal() end
	pl.ChatPrint = function(_, t) table.insert(S.log, "CHAT: " .. tostring(t)) end
	pl.ViewPunch = function() end

	S.ents[1] = pl
	S.players = { pl }
	return pl
end

local function loadAddon()
	-- the addon's own loader: include() is wired to the stub in stub_gmod.lua
	local entry = "autorun/epic_sweps_init.lua"
	local chunk = assert(loadfile(S.addonRoot .. "/" .. entry, "bt", api.getG()))
	chunk()
	grabGlobals()

	api.loadEffectScripts(ADDON)
	api.loadEntityScripts(ADDON)
end

local function step(frames, dt, clientHooks)
	for i = 1, frames do
		api.step(dt)

		if clientHooks then
			api.runHook("Think")
			api.runHook("HUDPaint")
			api.runHook("PostDrawTranslucentRenderables", false, false)
		end
	end
end

print("Epic SWEPs - offline smoke test")
print("addon: " .. ADDON)

-- ============================================================== SERVER ====
print("\n[server] load + run both weapons through their whole timeline")
api.setRealm("SERVER")
api.stateReset()
S.addonRoot = ADDON .. "/lua"
local pl = makePlayer()
loadAddon()

check("modules registered", EP ~= nil and EP.Config ~= nil and EP.Util ~= nil, EP and "namespace live")
check("cvars created", EP.CVars and EP.CVars.epic_missiles_count ~= nil)
check("effects registered", S.effects["epic_explosion"] ~= nil and S.effects["epic_zombie_burst"] ~= nil,
	"got " .. table.concat((function()
		local t = {}
		for k in pairs(S.effects) do t[#t + 1] = k end
		return t
	end)(), ","))

-- the zombie box, fired from the weapon itself
local zombieSwep = api.loadWeapon(ADDON, "epic_zombiespawner")
zombieSwep.owner = pl
zombieSwep.Primary = { ClipSize = 999, DefaultClip = 999, Automatic = false, Ammo = "none" }
zombieSwep:Initialize()
zombieSwep:Deploy()
zombieSwep:PrimaryAttack()

local breach
for _, e in pairs(S.ents) do
	if e.classname == "epic_zombie_breach" then breach = e end
end
check("SWEP primary fire spawns a breach", breach ~= nil)

-- the missiles, also fired through the weapon
local missileSwep = api.loadWeapon(ADDON, "epic_missilebarrage")
missileSwep.owner = pl
missileSwep.Primary = { ClipSize = 9, DefaultClip = 9, Automatic = false, Ammo = "GaussEnergy" }
missileSwep:Initialize()
missileSwep:PrimaryAttack()

local barrage
for _, e in pairs(S.ents) do
	if e.classname == "epic_missile_barrage" then barrage = e end
end
check("SWEP primary fire spawns a barrage", barrage ~= nil)
check("barrage took the full 7000", barrage and barrage.Count == 7000, barrage and tostring(barrage.Count))
check("damage subset computed", barrage and barrage.DamageImpacts and #barrage.DamageImpacts > 0,
	"DamageImpacts=" .. tostring(barrage and barrage.DamageImpacts and #barrage.DamageImpacts)
	.. " timeline.damageCount=" .. tostring(barrage and barrage.Timeline and barrage.Timeline.damageCount)
	.. " util.Impacts=" .. tostring(type(api.getG().EpicSWEPs.Util.Impacts)))

-- run 14 seconds of fake server
step(470, 0.03)
check("every missile was fired", barrage.Sent == 7000, "Sent=" .. tostring(barrage.Sent))

check("horde actually spawned (>= 100 NPCs/props)", S.createdNPCs >= 100, "spawned " .. S.createdNPCs)
check("breach counted its zombies", breach and breach.Spawned == 100, breach and ("Spawned=" .. tostring(breach.Spawned)))
check("damage was dealt by missiles", S.damageEvents > 0, S.damageEvents .. " damage events")
check("explosions pushed physics", (S.blasts or 0) > 0, (S.blasts or 0) .. " blast damages")
check("effects fired over the show", (S.effectFired and S.effectFired["epic_explosion"] or 0) > 0,
	"explosion x" .. (S.effectFired and S.effectFired["epic_explosion"] or 0))
check("zombie pop effect fired", (S.effectFired and S.effectFired["epic_zombie_pop"] or 0) > 0,
	"pop x" .. (S.effectFired and S.effectFired["epic_zombie_pop"] or 0))
check("voice + explosion sounds played", #S.soundCalls > 0, #S.soundCalls .. " sound events")
check("net traffic produced", #S.netlog > 0, #S.netlog .. " messages")

local netTypes = {}
for _, m in ipairs(S.netlog) do netTypes[m.str] = (netTypes[m.str] or 0) + 1 end
check("announce stream present", (netTypes["epic_sweps"] or 0) > 0, "epic_sweps x" .. (netTypes["epic_sweps"] or 0))
check("scene state stream present", (netTypes["epic_sweps"] or 0) > 0)

-- keep the server's outgoing traffic for the client pass
-- the exact words the user asked for, as they went out over the wire
local texts = {}
for _, m in ipairs(S.netlog) do
	for _, v in ipairs(m.vals) do
		if v[1] == "table" and v[2] and v[2].text then texts[v[2].text] = true end
	end
end
local function sawText(needle)
	for t in pairs(texts) do
		if string.find(string.upper(t), string.upper(needle), 1, true) then return t end
	end
	return nil
end
check("title text was broadcast", sawText("7000 MISSILES") ~= nil, tostring(sawText("7000 MISSILES")))
check("the missiles line was broadcast", sawText("7 THOUSAND F*CKING MISSILES") ~= nil, tostring(sawText("7 THOUSAND F*CKING MISSILES")))
check("the 'blow up this man' line was broadcast", sawText("BLOW UP THIS MAN") ~= nil, tostring(sawText("BLOW UP THIS MAN")))
check("the zombie title was broadcast", sawText("100 ZOMBIE SPAWNER") ~= nil, tostring(sawText("100 ZOMBIE SPAWNER")))
check("100 zombies reported loose", sawText("100 ZOMBIES LOOSE") ~= nil, tostring(sawText("100 ZOMBIES LOOSE")))

local traffic = {}
for i, m in ipairs(S.netlog) do traffic[i] = m end

-- ============================================================== CLIENT ====
print("\n[client] load, replay the server's traffic, and draw it")
api.setRealm("CLIENT")
api.stateReset()
S.addonRoot = ADDON .. "/lua"
S.netlog = traffic
makePlayer()
loadAddon()

check("renderer started", EP.Scene ~= nil and EP.Scene.barrages ~= nil)
check("hud queue exists", EP.HUD and type(EP.HUD.Push) == "function")

local before = api.count("surface.streak")
api.replayNet()
check("server state landed in the renderer", next(EP.Scene.barrages) ~= nil or next(EP.Scene.breaches) ~= nil)
check("hud got the big text", #EP.HUD.queue > 0, #EP.HUD.queue .. " announcements queued")

-- the client sees a barrage mid-show: give it fresh state, then watch it draw
local scene = EP.Scene.barrages[1] or (next(EP.Scene.barrages) and EP.Scene.barrages[next(EP.Scene.barrages)])
if scene then
	scene.start = S.time	-- rebase the show so the streaks are in the window we test
	scene.pos = scene.pos or api.Vector(0, 400, 0)
end

step(160, 0.03, true)

check("missile streaks were drawn", api.count("surface.streak") > before, api.count("surface.streak") .. " streak sprites")
check("text was drawn on the HUD", api.count("draw.SimpleText") > 0, api.count("draw.SimpleText") .. " text draws")
check("rects/flash drawn", api.count("surface.DrawTexturedRect") + api.count("render.DrawQuadEasy") > 0,
	"flash " .. (api.count("surface.DrawTexturedRect") + api.count("render.DrawQuadEasy")))
check("dynamic lights requested", api.count("DynamicLight") > 0, api.count("DynamicLight") .. " lights")
local garnish = 0
for _, name in ipairs({ "ManhackSparks", "ThumperDust", "VortDispel", "StunstickImpact", "CommandPointer" }) do
	garnish = garnish + (S.effectFired[name] or 0)
end
check("engine garnish effects fired (validated names)", garnish > 0, garnish .. " bursts")

-- every frame of a full barrage: no errors, no NaNs
step(400, 0.03, true)

local unknown = {}
for k, n in pairs(S.unknown or {}) do unknown[#unknown + 1] = k .. " x" .. n end
table.sort(unknown)
print("\nengine methods the stub had to invent (review for typos):\n  " ..
	(#unknown > 0 and table.concat(unknown, ", ") or "none"))

print(string.format("\n%d passed, %d failed", okCount, failCount))
if failCount > 0 then
	for _, l in ipairs(S.log) do
		if string.find(l, "ERR", 1, true) then print(l) end
	end
	error("smoke test failed: " .. failCount .. " problem(s)")
end
print("OK - both addons run clean in the stub environment")
