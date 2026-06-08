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
  TestPrimaryRaceARMA.pas

  Targeted experiment: does Skyrim's engine honor Additional Races for armor
  dispatch when crossing race "families" (vanilla -> UBE), or does it only
  match on primary RNAM?

  Test setup:
    1. Copy ImperialLightCuirassAA (00013ED3) into CBBEtoUBE_Compat.esp as a
       fresh new ARMA with its own FormID (not an override).
    2. On that new ARMA, set primary RNAM = 00UBE_RedguardRace (0305A18E).
       Clear the Additional Races list.
    3. Copy ImperialLightCuirass ARMO (00013ED9) into CBBEtoUBE_Compat.esp as
       an override. Add the new ARMA to its Armatures (MODL) list.

  After this script + Ctrl+S, in-game test:
    - Equip Imperial Light Armor on UBE Redguard
    - If it renders: engine only honors primary RNAM. Need to create new
      ARMAs (one per UBE race) for every armor. Approach 3 Phase 3 path.
    - If still invisible: engine uses something deeper. Approach 3 likely
      a dead end without insider UBE/engine knowledge.

  HOW TO USE:
    1. Open SSEEdit through MO2 (normal entry).
    2. Wait for "Background Loader: finished".
    3. In the left tree, right-click any plugin -> Apply Script
    4. Pick TestPrimaryRaceARMA
    5. Watch Messages tab for confirmation
    6. Ctrl+S to save changes
    7. Test in-game
}

unit TestPrimaryRaceARMA;

const
  PatchFileName    = 'CBBEtoUBE_Compat.esp';
  SourceARMA_FID   = $00013ED3;                 // ImperialCuirassLightAA
  SourceARMO_FID   = $00013ED9;                 // Imperial Light Armor (the ARMO)
  UBERedguardEDID  = '00UBE_RedguardRace';


var
  patchFile: IInterface;


// Find a record by load-order FormID in Skyrim.esm specifically. FormIDs in
// the form $000XXXXX are Skyrim.esm-rooted; we look at FileByLoadOrder(0).
function FindBySkyrimFormID(sig: string; fid: Cardinal): IInterface;
var
  i: Integer;
  grp, rec: IInterface;
begin
  Result := nil;
  grp := GroupBySignature(FileByLoadOrder(0), sig);
  if not Assigned(grp) then Exit;
  for i := 0 to ElementCount(grp) - 1 do begin
    rec := ElementByIndex(grp, i);
    if FormID(rec) = fid then begin
      Result := WinningOverride(rec);
      Exit;
    end;
  end;
end;


function FindRecordByEdid(sig: string; edid: string): IInterface;
var
  i, j: Integer;
  pl, grp, rec: IInterface;
begin
  Result := nil;
  for i := 0 to FileCount - 1 do begin
    pl := FileByIndex(i);
    grp := GroupBySignature(pl, sig);
    if not Assigned(grp) then Continue;
    for j := 0 to ElementCount(grp) - 1 do begin
      rec := WinningOverride(ElementByIndex(grp, j));
      if EditorID(rec) = edid then begin
        Result := rec;
        Exit;
      end;
    end;
  end;
end;


function GetOrCreatePatchFile: IInterface;
var
  i: Integer;
begin
  Result := nil;
  for i := 0 to FileCount - 1 do
    if GetFileName(FileByIndex(i)) = PatchFileName then begin
      Result := FileByIndex(i);
      AddMessage('using existing patch file ' + PatchFileName);
      Exit;
    end;
  Result := AddNewFileName(PatchFileName, False);
  if Assigned(Result) then
    AddMessage('created new patch file ' + PatchFileName)
  else
    AddMessage('FAILED to create patch file ' + PatchFileName);
end;


function Initialize: Integer;
var
  srcArma, srcArmo, ubeRace: IInterface;
  newArma, armoOverride, armatures, newEntry, addnl: IInterface;
  i: Integer;
begin
  Result := 0;

  patchFile := GetOrCreatePatchFile;
  if not Assigned(patchFile) then begin
    AddMessage('FATAL: could not get patch file'); Result := 1; Exit;
  end;

  AddMasterIfMissing(patchFile, 'Skyrim.esm');
  AddMasterIfMissing(patchFile, 'UBE_AllRace.esp');

  // 1. Find the source ARMA and the target UBE race.
  srcArma := FindBySkyrimFormID('ARMA', SourceARMA_FID);
  if not Assigned(srcArma) then begin
    AddMessage('FATAL: could not find ARMA by FormID 0x' + IntToHex(SourceARMA_FID, 8));
    Result := 1; Exit;
  end;
  AddMessage('source ARMA: ' + Name(srcArma));

  ubeRace := FindRecordByEdid('RACE', UBERedguardEDID);
  if not Assigned(ubeRace) then begin
    AddMessage('FATAL: could not find race ' + UBERedguardEDID); Result := 1; Exit;
  end;
  AddMessage('UBE race: ' + Name(ubeRace));

  // 2. Deep-copy the ARMA into our patch file as a BRAND NEW RECORD (new FormID).
  newArma := wbCopyElementToFile(srcArma, patchFile, True, True);
  if not Assigned(newArma) then begin
    AddMessage('FATAL: failed to deep-copy ARMA'); Result := 1; Exit;
  end;
  // Rename so we can find it later.
  SetElementEditValues(newArma, 'EDID', 'CBBEtoUBE_Test_ImperialLightCuirassAA');
  AddMessage('new ARMA created: ' + Name(newArma));

  // 3. Set primary race RNAM = 00UBE_RedguardRace
  SetNativeValue(ElementByPath(newArma, 'RNAM'), GetLoadOrderFormID(ubeRace));
  AddMessage('  primary RNAM set to ' + EditorID(ubeRace));

  // 4. Clear Additional Races on the new ARMA so we test primary alone.
  addnl := ElementByPath(newArma, 'Additional Races');
  if Assigned(addnl) then begin
    for i := ElementCount(addnl) - 1 downto 0 do
      RemoveByIndex(addnl, i, True);
    AddMessage('  cleared Additional Races');
  end;

  // 5. Find the ARMO that uses this ARMA, override it into our patch.
  srcArmo := FindBySkyrimFormID('ARMO', SourceARMO_FID);
  if not Assigned(srcArmo) then begin
    AddMessage('FATAL: could not find ARMO by FormID 0x' + IntToHex(SourceARMO_FID, 8));
    Result := 1; Exit;
  end;
  AddMessage('source ARMO: ' + Name(srcArmo));

  armoOverride := wbCopyElementToFile(srcArmo, patchFile, False, True);
  if not Assigned(armoOverride) then begin
    AddMessage('FATAL: failed to override ARMO'); Result := 1; Exit;
  end;

  // 6. Add the new ARMA to the ARMO's Armatures (MODL) list.
  armatures := ElementByPath(armoOverride, 'Armature');
  if not Assigned(armatures) then armatures := ElementByPath(armoOverride, 'MODL');
  if not Assigned(armatures) then begin
    AddMessage('FATAL: ARMO has no Armature/MODL element'); Result := 1; Exit;
  end;
  newEntry := ElementAssign(armatures, HighInteger, nil, False);
  SetNativeValue(newEntry, GetLoadOrderFormID(newArma));
  AddMessage('  added new ARMA to ARMO Armature list');

  AddMessage('========================================');
  AddMessage('TestPrimaryRaceARMA done.');
  AddMessage('Save via Ctrl+S, then test in-game:');
  AddMessage('  player.equipitem 00013ED9');
end;


function Process(e: IInterface): Integer;
begin
  Result := 1;
end;


function Finalize: Integer;
begin
  Result := 0;
end;

end.
