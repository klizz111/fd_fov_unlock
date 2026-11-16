--[[
    FDCamDump v4  —  Fallen Doll (Paralogue, UE 5.7.4)

    实测：UE4SS 在这套 exe 上任何 FProperty 访问都会崩
    （ForEachProperty / obj[prop] / DumpAllObjects / DumpJMAP 全试过，全崩）。
    但 UObject 指针、FName、UClass、Outer 完全可用。

    所以 v4 只做一件事：把相机相关对象的「名字 + 地址」吐出来，
    属性读取交给进程外的 ReadProcessMemory 去做（外部扫 float，不碰 FProperty）。

    热键：
      F6  object_manifest -> FDCamDump/objects.txt
      F8  同上，但把 Actor/Component 全域也列一遍（更大，慎用）

    输出目录：
      默认使用游戏根目录下的 Paralogue/Binaries/Win64/FDCamDump/
      如需指定游戏根目录，可设置 FDMOD_GAME_DIR 环境变量。

    输出行格式：
      CLASS_NAME<TAB>0xADDR<TAB>0xCLASS_ADDR<TAB>0xOUTER_ADDR<TAB>FULL_NAME
]]

local GAME_DIR = os.getenv("FDMOD_GAME_DIR")
local OUT = GAME_DIR
    and (GAME_DIR .. "/Paralogue/Binaries/Win64/FDCamDump/")
    or "Paralogue/Binaries/Win64/FDCamDump/"
local TAG = "[FDCamDump] "

local function log(m) print(TAG .. tostring(m) .. "\n") end

local function openf(name)
    local f, err = io.open(OUT .. name, "w")
    if not f then log("io.open failed: " .. OUT .. name .. " -> " .. tostring(err)) end
    return f
end

local function w(f, s) if f then f:write(tostring(s), "\n") end end

local function addr(o)
    local ok, a = pcall(function() return o:GetAddress() end)
    if ok and a then return string.format("0x%X", a) end
    return "0x0"
end

local function emit(f, o, seen)
    local full = select(2, pcall(function() return o:GetFullName() end))
    if not full then return false end
    if seen[full] then return false end
    seen[full] = true
    local cls = select(2, pcall(function() return o:GetClass() end))
    local cname = "?"
    local caddr = "0x0"
    if cls then
        cname = tostring(select(2, pcall(function() return cls:GetFullName() end)) or "?")
        caddr = addr(cls)
    end
    local outer = select(2, pcall(function() return o:GetOuter() end))
    local oaddr = outer and addr(outer) or "0x0"
    w(f, string.format("%s\t%s\t%s\t%s\t%s", cname, addr(o), caddr, oaddr, full))
    return true
end

-- 目标类：None = 用类名筛，All = 该类所有实例
local TARGETS = {
    "CameraComponent",
    "CineCameraComponent",
    "SpringArmComponent",
    "PlayerCameraManager",
    "PlayerController",
    "CameraActor",
    "SpectatorPawn",
    "DefaultPawn",
}

local function manifest()
    local f = openf("objects.txt")
    if not f then return end
    w(f, "FDCamDump v4 manifest  (names + addresses only, no FProperty access)")

    local seen = {}
    local n = 0
    for _, short in ipairs(TARGETS) do
        local objs = select(2, pcall(FindAllOf, short))
        if objs then
            w(f, string.format("# %s : %d instances", short, #objs))
            for _, o in pairs(objs) do
                if emit(f, o, seen) then n = n + 1 end
            end
        else
            w(f, string.format("# %s : FindAllOf -> nil", short))
        end
    end
    w(f, string.format("# total emitted: %d", n))
    f:close()
    log(string.format("F6 manifest done: %d objects -> objects.txt", n))
end

local function wide()
    local f = openf("objects_all.txt")
    if not f then return end
    w(f, "FDCamDump v4 wide manifest")
    local seen = {}
    local n = 0
    for _, short in ipairs({ "Actor", "ActorComponent", "SceneComponent" }) do
        local objs = select(2, pcall(FindAllOf, short))
        if objs then
            for _, o in pairs(objs) do
                if emit(f, o, seen) then n = n + 1 end
            end
        end
    end
    w(f, string.format("# total emitted: %d", n))
    f:close()
    log(string.format("F8 wide manifest done: %d objects", n))
end

local function bind(key, name, fn)
    local ok, err = pcall(function()
        RegisterKeyBind(key, function()
            log(">>> " .. name)
            local ok2, e2 = pcall(fn)
            if not ok2 then log("<<< " .. name .. " threw: " .. tostring(e2)) end
        end)
    end)
    if not ok then log("bind " .. name .. " failed: " .. tostring(err)) end
end

bind(Key.F6, "object_manifest", manifest)
bind(Key.F8, "wide_manifest", wide)

log("v4 loaded. F6=objects.txt(相机目标地址)  F8=objects_all.txt(全域)  不碰任何属性")
