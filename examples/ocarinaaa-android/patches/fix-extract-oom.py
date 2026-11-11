#!/usr/bin/env python3
"""Patch Shipwright-Ports after clone to stop ROM extraction OOMing ~77%.

ZAPD/OTRExporter keeps every extracted asset in a std::map until zip_close.
On Android that heap grows through dungeon XMLs (~77% of the XML list) and
the LMK kills the process. Write assets to disk and add them with
zip_source_file so libzip streams instead of retaining buffers.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else "Shipwright-Ports")
marker = root / "OTRExporter/OTRExporter/ExporterArchiveO2R.cpp"
if marker.exists() and "AddFileFromPath" in marker.read_text():
    print("extract-OOM patches already applied")
    sys.exit(0)

# --- 1. OTRExporter: disk-backed zip on Android --------------------------------
main = root / "OTRExporter/OTRExporter/Main.cpp"
t = main.read_text()
old_add = '''void AddFile(std::string fName, std::vector<char> data)
{
    if (Globals::Instance->fileMode != ZFileMode::ExtractDirectory)
        DiskFile::WriteAllBytes("Extract/" + fName, data);
    else
    {
        std::unique_lock Lock(fileMutex);
        files[fName] = data;
    }
}'''
new_add = '''void AddFile(std::string fName, std::vector<char> data)
{
    // Always spill to disk. Holding every asset in `files` until zip_close
    // OOMs Android around 77% (dungeon scene XMLs).
    DiskFile::WriteAllBytes("Extract/" + fName, data);
#ifndef __ANDROID__
    if (Globals::Instance->fileMode == ZFileMode::ExtractDirectory)
    {
        std::unique_lock Lock(fileMutex);
        files[fName] = data;
    }
#endif
}'''
if old_add not in t:
    raise SystemExit("AddFile() not found in OTRExporter Main.cpp")
t = t.replace(old_add, new_add, 1)

old_loop = '''        for (const auto& item : files)
        {
            std::string fName = item.first;
            if (fName.find("gTitleZeldaShieldLogoMQTex") != std::string::npos && !ZRom(romPath).IsMQ())
            {
                size_t pos = 0;
                if ((pos = fName.find("gTitleZeldaShieldLogoMQTex", 0)) != std::string::npos)
                {
                    fName.replace(pos, 27, "gTitleZeldaShieldLogoTex");
                }
            }
            const auto& fileData = item.second;
            archive->AddFile(fName, (void*)fileData.data(),\tfileData.size());
        }'''
new_loop = '''        {
            auto lst = Directory::ListFiles("Extract");
            for (auto item : lst)
            {
                std::string fName = item;
                auto split = StringHelper::Split(item, "Extract/");
                if (split.size() > 1) {
                    fName = split[1];
                }
                if (fName.find("gTitleZeldaShieldLogoMQTex") != std::string::npos && !ZRom(romPath).IsMQ())
                {
                    size_t pos = 0;
                    if ((pos = fName.find("gTitleZeldaShieldLogoMQTex", 0)) != std::string::npos)
                    {
                        fName.replace(pos, 27, "gTitleZeldaShieldLogoTex");
                    }
                }
                archive->AddFileFromPath(fName, item);
            }
        }'''
if old_loop not in t:
    raise SystemExit("files loop not found in OTRExporter Main.cpp")
t = t.replace(old_loop, new_loop, 1)
main.write_text(t)
print("patched", main)

# --- 2. zip_source_file so libzip does not retain buffers ----------------------
hdr = root / "OTRExporter/OTRExporter/ExporterArchive.h"
h = hdr.read_text()
if "AddFileFromPath" not in h:
    h = h.replace(
        "virtual bool AddFile(const std::string& filePath, void* fileData, size_t fileSize) = 0;",
        "virtual bool AddFile(const std::string& filePath, void* fileData, size_t fileSize) = 0;\n"
        "    virtual bool AddFileFromPath(const std::string& filePath, const std::string& diskPath) { (void)filePath; (void)diskPath; return false; }",
    )
    hdr.write_text(h)
    print("patched", hdr)

o2r_h = root / "OTRExporter/OTRExporter/ExporterArchiveO2R.h"
oh = o2r_h.read_text()
if "AddFileFromPath" not in oh:
    oh = oh.replace(
        "bool AddFile(const std::string& filePath, void* fileData, size_t fileSize) override;",
        "bool AddFile(const std::string& filePath, void* fileData, size_t fileSize) override;\n"
        "    bool AddFileFromPath(const std::string& filePath, const std::string& diskPath) override;",
    )
    o2r_h.write_text(oh)
    print("patched", o2r_h)

o2r = root / "OTRExporter/OTRExporter/ExporterArchiveO2R.cpp"
c = o2r.read_text()
if "AddFileFromPath" not in c:
    c += r'''
bool ExporterArchiveO2R::AddFileFromPath(const std::string& filePath, const std::string& diskPath) {
    zip_source_t* source = zip_source_file(mZip, diskPath.c_str(), 0, -1);
    if (source == nullptr) {
        zip_error_t* zipError = zip_get_error(mZip);
        SPDLOG_ERROR("Failed to create ZIP file source. Error: {}", zip_error_strerror(zipError));
        zip_error_fini(zipError);
        return false;
    }
    if (zip_file_add(mZip, filePath.c_str(), source, ZIP_FL_OVERWRITE | ZIP_FL_ENC_UTF_8) < 0) {
        zip_error_t* zipError = zip_get_error(mZip);
        SPDLOG_ERROR("Failed to add file to ZIP. Error: {}", zip_error_strerror(zipError));
        zip_source_free(source);
        zip_error_fini(zipError);
        return false;
    }
    return true;
}
'''
    o2r.write_text(c)
    print("patched", o2r)

# --- 3. CallZapd: copy only the ROM version's XMLs, not all 14 trees -----------
ext = root / "soh/soh/Extractor/Extract.cpp"
e = ext.read_text()
old_copy = '''#if defined(_WIN32) || defined(__ANDROID__)
    std::filesystem::copy(installPath + "/assets", tempdir + "/assets",
                          std::filesystem::copy_options::recursive | std::filesystem::copy_options::update_existing);
#else
    std::filesystem::create_symlink(installPath + "/assets", tempdir + "/assets");
#endif'''
new_copy = '''#if defined(_WIN32) || defined(__ANDROID__)
    // Copy only the XMLs for this ROM. The full extractor tree is ~55MB x 14
    // versions and filling /tmp mid-extract is a common Android crash ~77%.
    std::filesystem::create_directories(tempdir + "/assets/extractor");
    const char* ver = GetZapdVerStr();
    auto copy_if = [&](const std::string& rel) {
        auto src = std::filesystem::path(installPath) / rel;
        auto dst = std::filesystem::path(tempdir) / rel;
        if (std::filesystem::exists(src)) {
            std::filesystem::create_directories(dst.parent_path());
            if (std::filesystem::is_directory(src)) {
                std::filesystem::copy(src, dst, std::filesystem::copy_options::recursive |
                                                    std::filesystem::copy_options::update_existing);
            } else {
                std::filesystem::copy_file(src, dst, std::filesystem::copy_options::update_existing);
            }
        }
    };
    copy_if("assets/extractor/filelists");
    copy_if(std::string("assets/extractor/xmls/") + ver);
    copy_if(std::string("assets/extractor/Config_") + ver + ".xml");
    copy_if("assets/extractor/TexturePool.xml");
    copy_if("assets/extractor/symbols");
#else
    std::filesystem::create_symlink(installPath + "/assets", tempdir + "/assets");
#endif'''
if old_copy not in e:
    raise SystemExit("assets copy block not found in Extract.cpp")
e = e.replace(old_copy, new_copy, 1)

# Guard CallZapd filesystem errors so we don't abort the process
old_zapd = '''    zapd_report(argc, (char**)argv.data(), extractCount, totalExtract);

    std::filesystem::copy(otrFile, exportdir + "/" + otrFile, std::filesystem::copy_options::overwrite_existing);

    // Go back to where this game was executed from
    std::filesystem::current_path(curdir);
    std::filesystem::remove_all(tempdir);

    return false;
}'''
new_zapd = '''    try {
        zapd_report(argc, (char**)argv.data(), extractCount, totalExtract);
        if (std::filesystem::exists(otrFile)) {
            std::filesystem::copy(otrFile, exportdir + "/" + otrFile,
                                  std::filesystem::copy_options::overwrite_existing);
        } else {
            ShowErrorBox("Extraction failed",
                         "oot.o2r was not produced. Free some storage and try again.");
        }
    } catch (const std::exception& ex) {
        ShowErrorBox("Extraction failed", ex.what());
    }

    // Go back to where this game was executed from
    try {
        std::filesystem::current_path(curdir);
        std::filesystem::remove_all(tempdir);
    } catch (...) {
    }

    return false;
}'''
if old_zapd not in e:
    raise SystemExit("zapd_report tail not found in Extract.cpp")
e = e.replace(old_zapd, new_zapd, 1)
ext.write_text(e)
print("patched", ext)

# --- 4. largeHeap so the remaining peak has more room --------------------------
man = root / "Android/app/src/main/AndroidManifest.xml"
m = man.read_text()
if "android:largeHeap" not in m:
    m = m.replace(
        'android:hardwareAccelerated="true"',
        'android:hardwareAccelerated="true"\n        android:largeHeap="true"',
    )
    man.write_text(m)
    print("patched", man)

print("extract-OOM patches applied")
