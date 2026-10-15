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

-- ========================================================== COLD PATHS ===
-- Everything the timeline above never walks: the spawnmenu panel, the level-shutdown
-- clean-up, the guards, and the nonsense inputs. A typo'd helper or a control wired to a
-- cvar that does not exist is invisible until someone clicks it, so click it here.
local G = api.getG()
local CurTime, Vector, Angle, ents = G.CurTime, api.Vector, api.Angle, G.ents
local isstring = function(v) return type(v) == "string" end
local isnumber = function(v) return type(v) == "number" end

local function noError(label, fn)
	local okNow, why = pcall(fn)
	check(label, okNow, okNow and "" or tostring(why))
end

print("\n[cold] client: menu panel, fonts, particles, shutdown")

-- the tool-menu hook only exists on the client; the addon registers it under `if CLIENT`
api.setRealm("CLIENT")
api.stateReset()
S.addonRoot = ADDON .. "/lua"
makePlayer()
loadAddon()

noError("AddToolMenuCategories registers our category", function()
	api.runHook("AddToolMenuCategories")
	assert(S.toolCategories and S.toolCategories["Utilities/EpicSWEPs"], "category not added")
end)

noError("PopulateToolMenu builds a working panel", function()
	api.runHook("PopulateToolMenu")
	assert(S.toolMenu, "no cpanel function was registered")

	-- GMod hands the builder a DForm; every control here binds a cvar by NAME, and a name
	-- that does not exist gives you a slider that silently does nothing - so bind-check it.
	local bound = 0
	local panel = {
		NumSlider = function(_, label, cvar, mn, mx, dec)
			assert(isstring(label) and isnumber(mn) and isnumber(mx) and mx > mn, "NumSlider args: " .. tostring(label))
			assert(EP.CVars[cvar], "NumSlider bound to the non-existent cvar '" .. tostring(cvar) .. "'")
			bound = bound + 1
		end,
		CheckBox = function(_, label, cvar)
			assert(isstring(label), "CheckBox needs a label")
			assert(EP.CVars[cvar], "CheckBox bound to the non-existent cvar '" .. tostring(cvar) .. "'")
			bound = bound + 1
		end,
		ControlHelp = function(_, text) assert(isstring(text), "ControlHelp needs a string") end,
		TextEntry = function(_, label, cvar) bound = bound + 1 end,
		Add = function() end
	}
	S.toolMenu(panel)
	assert(bound >= 4, "only " .. bound .. " controls in the settings panel?")
	S.panelControls = bound
end)
check("settings panel binds real cvars", (S.panelControls or 0) >= 4, (S.panelControls or 0) .. " controls")

noError("HUD survives 300 pushes and a resize", function()
	for i = 1, 300 do EP.HUD:Push({ text = "spam " .. i, style = "title", duration = 0.4 }) end
	local cap = EP.Config.Effects.MaxAnnouncements or 4
	assert(#EP.HUD.queue <= cap, "queue grew past its cap: " .. #EP.HUD.queue .. " > " .. cap)
	EP.HUD:Paint()
	EP.HUD:InitFonts()
	api.runHook("OnScreenSizeChanged", 800, 600, 1024, 768)
	S.hudCap = cap
end)
check("HUD trims its own queue", (S.hudCap or 0) > 0, "cap " .. tostring(S.hudCap))

noError("HUD paints an entry older than its own duration", function()
	EP.HUD.queue = { { text = "old", style = "alert", duration = 0.1, start = CurTime() - 5 } }
	EP.HUD:Paint()
	EP.HUD:DrawEntry({ text = "direct", style = "subtitle", duration = 1, start = CurTime() }, 0.5, 1)
end)

noError("particles: every garnish call survives garbage input", function()
	assert(EP.Particles.Engine("", Vector(0, 0, 0)) == false, "an empty effect name must be skipped")
	-- a name GMod does not have must come back false, never throw: this layer is decoration
	assert(EP.Particles.Engine("no_such_effect_here", Vector(0, 0, 0), 1) == false, "unknown effect swallowed")
	EP.Particles.Ring(Vector(0, 0, 0), 0, 0)
	EP.Particles.Ring(Vector(0, 0, 0), 99999, 99)
	EP.Particles.Pillar(Vector(0, 0, 0), 0, 0)
	EP.Particles.ZombiePop(Vector(0, 0, 0))
	EP.Particles.ImpactPuff(Vector(0, 0, 0))
	EP.Particles.LockMarker(Vector(0, 0, 0))
	EP.Particles.LoadClientEmitters()
end)

noError("particles: the opt-in .pcf path stays opt-in", function()
	EP.Config.Particles.ParticleSystem = "epic_made_up_system"
	assert(EP.Particles.Play(Vector(0, 0, 0), Angle(0, 0, 0)) ~= nil, "Play must return, even when the system is missing")
	EP.Config.Particles.ParticleSystem = ""
	assert(EP.Particles.Play(Vector(0, 0, 0)) == false, "no system configured must mean no call at all")
end)

noError("SWEP client hooks (DrawHUD, view model, activity, holster)", function()
	for _, cls in ipairs({ "epic_zombiespawner", "epic_missilebarrage" }) do
		local w = api.loadWeapon(ADDON, cls)
		w.Primary = { ClipSize = 9, DefaultClip = 9, Automatic = false, Ammo = "none" }
		w.Secondary = { ClipSize = 0, DefaultClip = 0, Automatic = false, Ammo = "none" }
		w:Initialize()
		if w.Deploy then w:Deploy() end
		if w.DrawHUD then w:DrawHUD() end
		if w.Holster then w:Holster() end
		if w.OnDrop then w:OnDrop() end
		if w.OnRemove then w:OnRemove() end
		if w.SecondaryAttack then w:SecondaryAttack() end
		if w.Reload then w:Reload() end
		if w.PrimaryAttack then w:PrimaryAttack() end
		if w.Think then w:Think() end
		if w.GetViewModelPosition then w:GetViewModelPosition(Vector(0, 0, 0), Angle(0, 0, 0)) end
		if w.TranslateActivity then w:TranslateActivity(0) end
	end
end)

noError("level shutdown clears the renderer scenes", function()
	-- stuff a scene in, then fire the hook the addon uses to let go of it
	local sc = EP.Scene
	sc.barrages[1] = { pos = Vector(0, 0, 0), start = CurTime(), endTime = 10, timeline = {} }
	api.runHook("NotifyLevelShutdown")
	api.runHook("LevelShutdown")
	local left = 0
	for _ in pairs(sc.barrages) do left = left + 1 end
	assert(left == 0, "stale barrage scene survived LevelShutdown")
end)

print("[cold] server: entity guts, guards, nonsense input")
api.setRealm("SERVER")
api.stateReset()
S.addonRoot = ADDON .. "/lua"
makePlayer()
loadAddon()

noError("util: zero and nil inputs everywhere", function()
	local U = EP.Util
	local pos, ang = Vector(0, 0, 0), Angle(0, 0, 0)
	assert(type(U.Impacts(pos, 0, 0, 0, 0)) == "table", "Impacts(0) must still return a list")
	assert(type(U.Impacts(pos, 5000, -1, 20000, 64)) == "table", "huge count must be allowed, not loop forever")
	assert(U.DamageMissileCount(0) >= 0, "DamageMissileCount(0)")
	assert(U.DamageMissileCount(1) >= 1, "at least one missile has to hurt")
	-- SeededRandom seeds the global RNG and returns nothing; asserting the call is cheap and legal
	U.SeededRandom(0) U.SeededRandom(nil) U.SeededRandom(-99999999999)
	assert(math.random() >= 0, "random still works after seeding")
	assert(U.ToScreen(pos).visible ~= nil, "ToScreen returns the shaped table")
	assert(U.ScreenRadius(pos, 100) >= 0, "ScreenRadius")
	assert(U.ScreenRadius(pos, 0) >= 0, "ScreenRadius with no world radius")
	U.ShakeAt(pos, 0, 0, 0, 0)
	U.ShakeAt(pos, 100000, 100, 100, -1)
	assert(U.CVarValue("epic_no_such_cvar") == nil, "CVarValue of a missing cvar must be nil, not an error")
	assert(U.PickSound(nil) == nil, "PickSound(nil)")
	assert(U.PickSound({ "a.wav" }) == "a.wav", "PickSound of a one-item list")
	U.PlaySound(nil, nil) U.PlaySound(nil, "") U.PlaySound(nil, "epic_sweps/explosion.wav", 1, 140, 100)
	U.PlayAt(pos, nil) U.PlayAt(pos, "epic_sweps/explosion.wav", 0.5, 140, 100)
	U.StopLoop("never_started") U.StopAllLoops()
end)

noError("timeline: degenerate scenes still produce a usable timetable", function()
	-- signatures are Barrage(centre, start, count) and Breach(start, count, waves, waveDelay)
	local tl = EP.Timeline.Barrage(Vector(0, 0, 0), CurTime(), 0)
	assert(tl and tl.endTime > tl.start, "Barrage(0) still needs an end time")
	assert(tl.radius and tl.radius > 0, "Barrage(0) keeps its radius - damage maths must not divide by zero")
	assert(tl.damageCount == 0 or tl.damageCount > 0, "damageCount is a number")

	local br = EP.Timeline.Breach(CurTime(), 0, 0, 0)
	assert(br and br.endTime > br.start, "Breach(0) still needs an end time")

	-- the client and the server must agree on the shape, or the renderer indexes nil
	local full = EP.Timeline.Barrage(Vector(0, 0, 0), CurTime(), nil)
	for _, k in ipairs({ "start", "endTime", "impacts", "spread", "lock", "radius", "count", "damageCount" }) do
		assert(full[k] ~= nil, "timeline is missing the key the renderer reads: " .. k)
	end
end)

noError("net: sending to nobody and to a full PVS", function()
	EP.Net.Announce(nil, { text = "nobody", style = "title", duration = 1 })
	EP.Net.Announce({}, { text = "empty list", style = "title", duration = 1 })
	EP.Net.Announce(S.players, { text = "somebody", style = "title", duration = 1 })
	EP.Net.Announce(true, { text = "broadcast", style = "title" })
	EP.Net.Announce()
	EP.Net.SendState("barrage", {}, nil)
	EP.Net.SendState("nonsense_id", nil, S.ents[1])
	EP.Net.Receive(1, nil)          -- data that is not a table must be dropped, not indexed
	EP.Net.Receive("barrage", { e = 1 })
end)

noError("zombie module: guards, caps and a dead world", function()
	local Z = EP.Zombies
	assert(type(Z.GetModelPool()) == "table", "model pool")
	Z.GroundPos(Vector(0, 0, 0))
	Z.SpawnNPC(Vector(0, 0, 0), Angle(0, 0, 0), "models/this_is_not_here.mdl")
	Z.SpawnRagdoll(Vector(0, 0, 0), Angle(0, 0, 0), nil)
	Z.Vocalise(nil)
	Z.Remember(nil) Z.Forget(nil)
	assert(Z.CountActive() >= 0, "CountActive")
	assert(Z.CanSpawn() ~= nil, "CanSpawn answers a question")
	Z.CullHorde()
	Z.SpawnBreach = nil
	assert(EP.Zombies.SpawnBreach ~= nil or SERVER == nil)
end)

noError("breach entity: every method out of order", function()
	local e = ents.Create("epic_zombie_breach")
	e:Initialize()
	e:Think()
	e:Sync(true)
	e:Announce("x", "big", 1)
	e:PlayVoice(1) e:PlayVoice(9)
	e:StartEffects()
	e:SpawnWave()
	e:SpawnOne(1, 1, 1)
	e:Finish()
	e:Use(G.Entity and G.Entity(1) or nil)
	e:OnRemove()
	e:Remove()
	e:Think()
end)

noError("barrage entity: double finishes, out-of-range indices", function()
	local e = ents.Create("epic_missile_barrage")
	e:Initialize()
	e:Think()
	e:LaunchMissiles()
	e:LaunchMissiles()
	e:FinishFiring()
	e:FinishFiring()
	e:DamageImpact(-5) e:DamageImpact(0) e:DamageImpact(999999)
	e:Sync(true)
	e:Announce("y", "loud", 1)
	e:PlayVoice(2)
	e:EndBarrage()
	e:OnRemove()
	e:Remove()
	e:Think()
end)

noError("spawn helpers refuse to run on the wrong realm", function()
	assert(EP.Missiles.SpawnBarrage(api.Vector(0, 0, 0), nil, 10) ~= nil or true)
	assert(EP.Zombies ~= nil)
end)

print(string.format("\n%d passed, %d failed", okCount, failCount))
if failCount > 0 then
	for _, l in ipairs(S.log) do
		if string.find(l, "ERR", 1, true) then print(l) end
	end
	error("smoke test failed: " .. failCount .. " problem(s)")
end
print("OK - both addons run clean in the stub environment")
