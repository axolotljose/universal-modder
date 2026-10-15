local C = EpicSWEPs and EpicSWEPs.Config or {}

-- ------------------------------------------------------------- console ---
-- Everything the two weapons need to be tuned without opening a text editor.
EpicSWEPs.CVars = {}

for name, def in pairs(C.CVars or {}) do
	if isnumber(def) then
		def = { default = def }
	end

	-- ARCHIVE keeps a value between sessions. FCVAR_REPLICATED (server side only) additionally makes the
	-- server's value law on every client, which is what we want for the two counts - the HUD has to show
	-- what the server is actually spawning - and deliberately NOT for the presentation toggles, because a
	-- replicated+archived cvar overwrites the player's own saved choice on every connect.
	local flags = FCVAR_ARCHIVE

	if SERVER and def.replicate then
		flags = flags + FCVAR_REPLICATED
	end

	EpicSWEPs.CVars[name] = CreateConVar(name, tostring(def.default or 0), flags, def.help or "Epic SWEPs")
end

-- ------------------------------------------------------- spawnmenu panel --
-- This is just a handy place to poke the numbers. The icons are separate: GMod
-- reads materials/vgui/entities/<class>.(tga|png) for weapons and entities, and if
-- you would rather point them somewhere else, set EpicSWEPs.Icons below.

-- Icons need no code: GMod reads materials/vgui/entities/<class>.(tga|png|jpg) by
-- itself, and the addon ships a 128x128 png for each weapon. To use other artwork,
-- convert it with VTFEdit and drop it in under that path and file name.

if CLIENT then
	hook.Add("AddToolMenuCategories", "EpicSWEPs.Menu", function()
		spawnmenu.AddToolCategory("Utilities", "EpicSWEPs", "Epic SWEPs")
	end)

	hook.Add("PopulateToolMenu", "EpicSWEPs.Menu", function()
		spawnmenu.AddToolMenuOption("Utilities", "EpicSWEPs", "EpicSWEPsSettings", "Epic SWEPs", "", "", function(panel)
			panel:ControlHelp("100 ZOMBIE SPAWNER + 7000 F*CKING MISSILES.\n" ..
				"Deep config: addons/epic_sweps/lua/epic_sweps/shared/epic_sweps_config.lua")

			panel:ControlHelp("\n-- 100 zombie spawner --")
			panel:NumSlider("Zombies per box", "epic_zombies_count", 1, 1000, 0)
			panel:CheckBox("Skip the waves (instant, painful)", "epic_zombies_instant")

			panel:ControlHelp("\n-- 7000 missiles --")
			panel:NumSlider("Missiles per barrage", "epic_missiles_count", 100, 20000, 0)

			panel:ControlHelp("\n-- presentation --")
			panel:CheckBox("Big on-screen text", "epic_show_text")
			panel:CheckBox("Voice lines", "epic_play_voice")
			panel:CheckBox("Screen shake", "epic_screen_shake")
		end)
	end)
end

-- ------------------------------------------------------------- clean-up ---
if CLIENT then
	local function stopLoops()
		EpicSWEPs.Util.StopAllLoops()

		-- the renderer keeps the live scenes in EpicSWEPs.Scene (its module-level table is the same one),
		-- and both die with the map, so empty them instead of leaving 7000 stale impact lists around
		local sc = EpicSWEPs.Scene
		if sc then
			sc.barrages = {}
			sc.breaches = {}
		end
	end

	hook.Add("NotifyLevelShutdown", "EpicSWEPs.Loops", stopLoops)
	hook.Add("LevelShutdown", "EpicSWEPs.Loops", stopLoops)
end

return EpicSWEPs.CVars
