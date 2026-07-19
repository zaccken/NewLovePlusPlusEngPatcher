"""EngPatcher assets/images folder → img.bin package index + .arc name."""

from __future__ import annotations

# folder basename (without .arc/.check) → (package_index, arc_filename)
# Prefer primary ARC packages (not the empty 5400 catalog).
IMAGE_MAP: dict[str, tuple[int, str]] = {
    "album": (4149, "Album.arc"),
    "amscratch": (4150, "AMScratch.arc"),
    "camerasetting": (4151, "CameraSetting.arc"),
    "card": (4152, "Card.arc"),
    "map_layout": (4074, "map_layout.arc"),
    "cmdjudge": (4182, "CmdJudge.arc"),
    "commu_option": (4185, "Commu_Option.arc"),
    "contest": (4186, "Contest.arc"),
    "datadelete": (4187, "DataDelete.arc"),
    "dataexport": (4188, "DataExport.arc"),
    "dataimport": (4189, "DataImport.arc"),
    "dateeditbase01": (4195, "DateEditBase01.arc"),
    "dateeditbase02": (4196, "DateEditBase02.arc"),
    "dateeditbase03": (4197, "DateEditBase03.arc"),
    "dateeditcom": (4198, "DateEditCom.arc"),
    "dateeditmaskedit": (4199, "DateEditMaskEdit.arc"),
    "fileselect": (5152, "FileSelect.arc"),
    "gallery_common": (5153, "Gallery_Common.arc"),
    "girlstalk": (5189, "GirlsTalk.arc"),
    "inputctexture": (5190, "InputCTexture.arc"),
    "inputntexture": (5190, "InputNTexture.arc"),
    "item": (5198, "Item.arc"),
    "itemselect": (5199, "ItemSelect.arc"),
    "loveplusmode": (5202, "LovePlusMode.arc"),
    "lpm_info": (5205, "LPM_Info.arc"),
    "mail": (5207, "Mail.arc"),
    "meetingdate": (5209, "MeetingDate.arc"),
    "meetingtime": (5210, "MeetingTime.arc"),
    "ncommonicon": (5238, "NCommonIcon.arc"),
    "ncommonmsel(8)": (5240, "NCommonMSel.arc"),
    "ncommonmsel(7)": (5241, "NCommonMSel.arc"),
    "ncommonmsel(6)": (5242, "NCommonMSel.arc"),
    "ncommonmsel(4)": (5244, "NCommonMSel.arc"),
    "ncommonmsel(3)": (5245, "NCommonMSel.arc"),
    "ncommonmsel": (5246, "NCommonMSel.arc"),
    "option": (5247, "Option.arc"),
    "option06": (5248, "Option06.arc"),
    "optionclock": (5249, "OptionClock.arc"),
    "profile": (5252, "Profile.arc"),
    "quest": (5253, "Quest.arc"),
    "scdkareshi": (5254, "ScdKareshi.arc"),
    "scdstatus": (5255, "ScdStatus.arc"),
    "schedule": (5256, "Schedule.arc"),
    "shop": (5258, "Shop.arc"),
    "syspopup": (5259, "SysPopup.arc"),
    "telephone": (5260, "Telephone.arc"),
    "title": (5261, "Title.arc"),
    "townguide": (5265, "TownGuide.arc"),
    "townvoice": (5266, "TownVoice.arc"),
    "web": (5316, "Web.arc"),
    "camera_btn01": (5323, "Camera_Btn01.arc"),
    "camera_btn02_c": (5324, "Camera_Btn02_c.arc"),
    "camera_btn02_l": (5324, "Camera_Btn02_l.arc"),
    "camera_btn03": (5325, "Camera_Btn03.arc"),
    "camera_btn04": (5326, "Camera_Btn04.arc"),
    "camera_pv03": (5351, "Camera_Pv03.arc"),
    "myroom": (5380, "Myroom.arc"),
    "dateedit": (5382, "DateEdit.arc"),
    "dateeditalbum": (5383, "DateEditAlbum.arc"),
    "dateeditcamera": (5384, "DateEditCamera.arc"),
    "dateeditmain": (5385, "DateEditMain.arc"),
    "gallery": (5394, "Gallery.arc"),
    "clockinfo": (5402, "ClockInfo.arc"),
    "kareshipower": (5501, "KareshiPower.arc"),
    "transfer": (5564, "Transfer.arc"),
    "myroomheader": (5575, "MyroomHeader.arc"),
}


def normalize_folder_key(folder_name: str) -> str:
    name = folder_name.strip()
    lower = name.lower()
    if lower.endswith(".check"):
        name = name[: -len(".check")]
    elif lower.endswith(".arc"):
        name = name[: -len(".arc")]
    return name.lower()


def resolve_folder(folder_name: str) -> tuple[int, str] | None:
    return IMAGE_MAP.get(normalize_folder_key(folder_name))
