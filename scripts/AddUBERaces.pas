// CBBEtoUBE - CBBE/3BA to UBE armor converter
// Copyright (C) 2026 DayOnly
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

{
  AddUBERaces.pas

  xEdit (SSEEdit) script that creates a new patch plugin and writes ARMA
  overrides into it. Each override extends the "Additional Races" list with
  the UBE variant of any vanilla humanoid race already on the ARMA.

  After running this and saving, CBBE-shape armors are visible on UBE
  characters because the engine now finds an ARMA match for the UBE race.

  Output plugin: CBBEtoUBE_Compat.esp (created in the user's Data folder, or
  in MO2's overwrite if launched through MO2). The user enables this single
  plugin in their load order.

  AUTOMATED USAGE:
    Launched via -script:AddUBERaces. No user clicks required - the script's
    Initialize phase iterates every plugin and every ARMA, creates the patch
    plugin if needed, copies records as overrides, and adds the UBE races.
    User then saves via Ctrl+S.
}

unit AddUBERaces;

const
  PatchFileName = 'CBBEtoUBE_Compat.esp';

  // Vanilla -> UBE race EditorID mapping. 8 humanoid + 8 vampire = 16 entries.
  RaceMapHumanoid =
    'NordRace,BretonRace,ImperialRace,RedguardRace,DarkElfRace,HighElfRace,WoodElfRace,OrcRace,' +
    'NordRaceVampire,BretonRaceVampire,ImperialRaceVampire,RedguardRaceVampire,' +
    'DarkElfRaceVampire,HighElfRaceVampire,WoodElfRaceVampire,OrcRaceVampire';
  RaceMapHumanoidUBE =
    '00UBE_NordRace,00UBE_BretonRace,00UBE_ImperialRace,00UBE_RedguardRace,' +
    '00UBE_DarkElfRace,00UBE_HighElfRace,00UBE_WoodElfRace,00UBE_OrcRace,' +
    '00UBE_NordRaceVampire,00UBE_BretonRaceVampire,00UBE_ImperialRaceVampire,' +
    '00UBE_RedguardRaceVampire,00UBE_DarkElfRaceVampire,00UBE_HighElfRaceVampire,' +
    '00UBE_WoodElfRaceVampire,00UBE_OrcRaceVampire';


var
  vanillaList:  TStringList;
  ubeList:      TStringList;
  ubeRaceCache: TStringList;     // edid -> IInterface (Objects[])
  patchFile:    IInterface;      // Our output patch plugin
  patched:      Integer;
  skipped:      Integer;
  armaCount:    Integer;
  ubeMastersAdded: Boolean;


function FindUBEEdidFor(vanillaEdid: string): string;
var
  idx: Integer;
begin
  idx := vanillaList.IndexOf(vanillaEdid);
  if idx >= 0 then Result := ubeList[idx]
  else Result := '';
end;


// Cache UBE race records by EditorID for O(1) lookup. Scans all loaded
// plugins once on first call.
function GetUBERace(edid: string): IInterface;
var
  i, j, idx: Integer;
  pl, races, rec: IInterface;
begin
  Result := nil;
  if not Assigned(ubeRaceCache) then begin
    ubeRaceCache := TStringList.Create;
    ubeRaceCache.Sorted := True;
    for i := 0 to FileCount - 1 do begin
      pl := FileByIndex(i);
      races := GroupBySignature(pl, 'RACE');
      if not Assigned(races) then Continue;
      for j := 0 to ElementCount(races) - 1 do begin
        rec := ElementByIndex(races, j);
        if Pos('00UBE_', EditorID(rec)) = 1 then
          ubeRaceCache.AddObject(EditorID(rec), rec);
      end;
    end;
    AddMessage('Cached ' + IntToStr(ubeRaceCache.Count) + ' UBE race records');
  end;

  idx := ubeRaceCache.IndexOf(edid);
  if idx >= 0 then Result := ObjectToElement(ubeRaceCache.Objects[idx]);
end;


// Find existing PatchFileName in the load order, or create it fresh.
function GetOrCreatePatchFile: IInterface;
var
  i: Integer;
begin
  Result := nil;
  for i := 0 to FileCount - 1 do
    if GetFileName(FileByIndex(i)) = PatchFileName then begin
      Result := FileByIndex(i);
      AddMessage('reusing existing patch file ' + PatchFileName);
      Exit;
    end;
  Result := AddNewFileName(PatchFileName, False);
  if Assigned(Result) then
    AddMessage('created new patch file ' + PatchFileName)
  else
    AddMessage('FAILED to create patch file ' + PatchFileName);
end;


function AdditionalRacesContains(races: IInterface; targetRace: IInterface): Boolean;
var
  i: Integer;
  entry, linked: IInterface;
  targetFid: Cardinal;
begin
  Result := False;
  if not Assigned(races) then Exit;
  if not Assigned(targetRace) then Exit;
  targetFid := GetLoadOrderFormID(targetRace);
  for i := 0 to ElementCount(races) - 1 do begin
    entry := ElementByIndex(races, i);
    linked := LinksTo(entry);
    if Assigned(linked) and (GetLoadOrderFormID(linked) = targetFid) then begin
      Result := True;
      Exit;
    end;
  end;
end;


// Patch one source ARMA: copy it as an override into our patch file, then
// add UBE races to the override's Additional Races list.
procedure PatchARMA(srcRec: IInterface);
var
  primaryRace, raceRef, ubeRace, addnl, newEntry, override, srcFile: IInterface;
  primaryEdid, srcFileName: string;
  i: Integer;
  vanillaRaces: TStringList;
begin
  if Signature(srcRec) <> 'ARMA' then Exit;
  Inc(armaCount);

  vanillaRaces := TStringList.Create;
  try
    vanillaRaces.Sorted := True;
    vanillaRaces.Duplicates := dupIgnore;

    // Collect vanilla human races already targeted by this ARMA (primary + additional)
    primaryRace := LinksTo(ElementByPath(srcRec, 'RNAM'));
    if Assigned(primaryRace) then begin
      primaryEdid := EditorID(primaryRace);
      if vanillaList.IndexOf(primaryEdid) >= 0 then
        vanillaRaces.Add(primaryEdid);
    end;

    addnl := ElementByPath(srcRec, 'Additional Races');
    if Assigned(addnl) then begin
      for i := 0 to ElementCount(addnl) - 1 do begin
        raceRef := LinksTo(ElementByIndex(addnl, i));
        if Assigned(raceRef) and (vanillaList.IndexOf(EditorID(raceRef)) >= 0) then
          vanillaRaces.Add(EditorID(raceRef));
      end;
    end;

    if vanillaRaces.Count = 0 then begin
      Inc(skipped);
      Exit;
    end;

    // Make sure the source plugin is a master of the patch file. Adding it
    // here is the no-op case if Initialize already pulled it in.
    srcFile := GetFile(srcRec);
    srcFileName := GetFileName(srcFile);
    if srcFileName <> PatchFileName then
      AddMasterIfMissing(patchFile, srcFileName);

    override := wbCopyElementToFile(srcRec, patchFile, False, True);
    if not Assigned(override) then begin
      AddMessage('  ! failed to copy ' + Name(srcRec) + ' to patch file');
      Exit;
    end;

    // Try the user-friendly path name first; fall back to the raw subrecord
    // signature if xEdit's path lookup doesn't recognize 'Additional Races'.
    addnl := ElementByPath(override, 'Additional Races');
    if not Assigned(addnl) then addnl := ElementByPath(override, 'MODL');
    if not Assigned(addnl) then
      addnl := Add(override, 'Additional Races', True);
    if not Assigned(addnl) then
      addnl := Add(override, 'MODL', True);

    for i := 0 to vanillaRaces.Count - 1 do begin
      ubeRace := GetUBERace(FindUBEEdidFor(vanillaRaces[i]));
      if not Assigned(ubeRace) then Continue;
      if AdditionalRacesContains(addnl, ubeRace) then Continue;

      // Use SetNativeValue with the load-order FormID - more robust than
      // SetEditValue with xEdit's display-format string parser. The patch
      // file already has UBE_AllRace.esp as a master (added in Initialize),
      // so the FormID mapping resolves.
      newEntry := ElementAssign(addnl, HighInteger, nil, False);
      SetNativeValue(newEntry, GetLoadOrderFormID(ubeRace));
      AddMessage('  + ' + Name(srcRec) + ': added ' + EditorID(ubeRace));
      Inc(patched);
    end;
  finally
    vanillaRaces.Free;
  end;
end;


function Initialize: Integer;
var
  i, j: Integer;
  pl, armaGrp: IInterface;
begin
  vanillaList := TStringList.Create;
  ubeList     := TStringList.Create;
  vanillaList.CommaText := RaceMapHumanoid;
  ubeList.CommaText     := RaceMapHumanoidUBE;
  patched := 0; skipped := 0; armaCount := 0;
  ubeMastersAdded := False;

  if vanillaList.Count <> ubeList.Count then begin
    AddMessage('FATAL: vanilla/UBE race lists are different lengths');
    Result := 1; Exit;
  end;
  AddMessage('AddUBERaces: ' + IntToStr(vanillaList.Count) + ' race mappings loaded');

  patchFile := GetOrCreatePatchFile;
  if not Assigned(patchFile) then begin
    AddMessage('FATAL: could not get or create ' + PatchFileName);
    Result := 1; Exit;
  end;

  // Add masters up-front, before any record copies. Adding masters AFTER
  // references are written can leave xEdit's FormID maps inconsistent.
  // UBE_AllRace.esp is mandatory (we reference UBE races from it).
  // Skyrim.esm is also mandatory (almost every ARMA we patch comes from it
  // or references a vanilla race in it).
  AddMasterIfMissing(patchFile, 'Skyrim.esm');
  AddMasterIfMissing(patchFile, 'UBE_AllRace.esp');
  AddMessage('declared masters: Skyrim.esm, UBE_AllRace.esp');

  // Iterate every loaded plugin's ARMA group. Skip our own patch file to
  // avoid infinite override loops.
  for i := 0 to FileCount - 1 do begin
    pl := FileByIndex(i);
    if GetFileName(pl) = PatchFileName then Continue;
    armaGrp := GroupBySignature(pl, 'ARMA');
    if not Assigned(armaGrp) then Continue;
    for j := 0 to ElementCount(armaGrp) - 1 do
      PatchARMA(ElementByIndex(armaGrp, j));
  end;

  AddMessage('========================================');
  AddMessage('AddUBERaces done.');
  AddMessage('  ARMA records visited: ' + IntToStr(armaCount));
  AddMessage('  Race additions written: ' + IntToStr(patched));
  AddMessage('  ARMAs skipped (no vanilla-human race): ' + IntToStr(skipped));
  AddMessage('Save via Ctrl+S to write ' + PatchFileName + ' to disk.');
  Result := 0;
end;


function Process(e: IInterface): Integer;
begin
  Result := 1;  // skip per-record phase; everything was done in Initialize
end;


function Finalize: Integer;
begin
  vanillaList.Free;
  ubeList.Free;
  if Assigned(ubeRaceCache) then ubeRaceCache.Free;
  Result := 0;
end;

end.
