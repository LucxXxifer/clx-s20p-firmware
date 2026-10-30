-- Contract regression: nixio modes are octal digit strings, not POSIX masks.
local here = arg[0]:match("^(.*)/tests/") or "."
local adapter = assert(dofile(here .. "/luci/model/s20p_dashboard.lua"))
local directory="/tmp/s20p-dashboard-v1"
local files,stats,locks={},{},{}
local mkdir_calls,open_calls,chmod_calls,rename_calls,sleeps=0,0,0,0,0
local function mode(actual,expected)
  assert(type(actual)=="string" and actual==expected,
    "nixio mode must be octal digit string "..expected..", got "..tostring(actual))
end
package.loaded["luci.jsonc"]={parse=function(s)return s=="STATE" and {version=1} or {} end,
  stringify=function()return "STATE" end}
package.loaded["luci.util"]={ubus=function()return {interface={}} end}
package.loaded["uci"]={cursor=function()return {get_all=function()return {} end} end}
package.loaded["nixio.fs"]={
  lstat=function(path)return stats[path] end,
  mkdir=function(path,value)mode(value,"700");mkdir_calls=mkdir_calls+1;stats[path]={type="dir",uid=0};return true end,
  chmod=function(path,value)mode(value,path==directory and "700" or "600");chmod_calls=chmod_calls+1;return true end,
  rename=function(from,to)files[to]=files[from];stats[to]={type="reg",uid=0,size=#files[to]};files[from]=nil;rename_calls=rename_calls+1;return true end,
  unlink=function(path)files[path]=nil;stats[path]=nil;return true end,
}
package.loaded["nixio"]={
  open_flags=function(a,b)assert(a=="wronly" and b=="creat");return 65 end,
  open=function(path,flags,value)
    mode(value,"600");assert(flags==65);open_calls=open_calls+1;stats[path]={type="reg",uid=0}
    return {lock=function(_,flag)locks[#locks+1]=flag;return true end,close=function()return true end}
  end,
  gettimeofday=function()return 1700000000,250000 end,
  nanosleep=function(seconds,nanoseconds)assert(seconds==0 and nanoseconds==100000000);sleeps=sleeps+1;return true end,
}
local original_open,original_popen=io.open,io.popen
io.open=function(path,access)
  if path==directory.."/state.new" and access=="w" then
    return {write=function(_,s)files[path]=s;return true end,flush=function()return true end,close=function()return true end}
  end
  if path==directory.."/state.json" and access=="r" and files[path] then
    return {read=function()return files[path] end,close=function()return true end}
  end
  error("unexpected production read: "..tostring(path))
end
io.popen=function(command,access)
  assert(command=="ip -j -4 route show table main default 2>/dev/null" and access=="r")
  return {read=function()return "ROUTES" end,close=function()return true end}
end
local ok,err=pcall(function()
  local env=adapter.production_env()
  local release=assert(env.lock())
  assert(mkdir_calls==1 and open_calls==1 and locks[1]=="tlock")
  release();assert(locks[2]=="ulock")
  assert(env.store({version=1}))
  assert(chmod_calls==2 and rename_calls==1)
  assert(env.load().version==1)
  assert(env.epoch()==1700000000.25)
  env.sleep(0.1);assert(sleeps==1)
  assert(type(env.routes())=="table")
end)
io.open,io.popen=original_open,original_popen
assert(ok,err)
print("passed production nixio filesystem, lock, time and route API contract test")
