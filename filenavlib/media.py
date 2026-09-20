"""Best-effort metadata extraction for image and video files.

Every function here is defensive: a corrupt, unsupported, or partially
readable file should degrade to a smaller dict (or None) rather than raise,
since a scan of an entire drive will inevitably hit odd files.
"""


def _rational_to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return value.numerator / value.denominator
        except (AttributeError, ZeroDivisionError):
            return None


def _gps_to_decimal(coord, ref):
    if not coord or len(coord) != 3:
        return None
    parts = [_rational_to_float(c) for c in coord]
    if any(p is None for p in parts):
        return None
    degrees, minutes, seconds = parts
    value = degrees + minutes / 60.0 + seconds / 3600.0
    if ref in ("S", "W"):
        value = -value
    return value


def get_image_info(path):
    """Return dimensions and, when present, EXIF capture/location/device info."""
    try:
        from PIL import Image, ExifTags
    except ImportError:
        return {"error": "Pillow not installed; image metadata unavailable"}

    info = {}
    try:
        with Image.open(path) as img:
            info["width"] = img.width
            info["height"] = img.height
            info["format"] = img.format

            try:
                exif = img.getexif()
            except Exception:
                exif = None

            if exif:
                tag_names = {v: k for k, v in ExifTags.TAGS.items()}
                for name in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
                    tag_id = tag_names.get(name)
                    if tag_id in exif:
                        info.setdefault("date_taken", str(exif[tag_id]))
                        break

                make = exif.get(tag_names.get("Make"))
                model = exif.get(tag_names.get("Model"))
                if make:
                    info["device_make"] = str(make).strip().strip("\x00")
                if model:
                    info["device_model"] = str(model).strip().strip("\x00")

                try:
                    gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
                except Exception:
                    gps_ifd = None
                if gps_ifd:
                    gps_tag_names = {v: k for k, v in ExifTags.GPSTAGS.items()}
                    lat = _gps_to_decimal(
                        gps_ifd.get(gps_tag_names.get("GPSLatitude")),
                        gps_ifd.get(gps_tag_names.get("GPSLatitudeRef")),
                    )
                    lon = _gps_to_decimal(
                        gps_ifd.get(gps_tag_names.get("GPSLongitude")),
                        gps_ifd.get(gps_tag_names.get("GPSLongitudeRef")),
                    )
                    if lat is not None and lon is not None:
                        info["gps_latitude"] = lat
                        info["gps_longitude"] = lon
                    alt = gps_ifd.get(gps_tag_names.get("GPSAltitude"))
                    if alt is not None:
                        alt_val = _rational_to_float(alt)
                        if alt_val is not None:
                            info["gps_altitude_m"] = alt_val
    except Exception as exc:
        info["error"] = f"could not read image metadata: {exc}"

    return info or None


def get_video_info(path):
    """Return duration/dimensions/device info via hachoir, when available."""
    try:
        from hachoir.parser import createParser
        from hachoir.metadata import extractMetadata
    except ImportError:
        return {"error": "hachoir not installed; video metadata unavailable"}

    info = {}
    parser = None
    try:
        parser = createParser(str(path))
        if parser is None:
            return {"error": "unrecognized video format"}
        with parser:
            metadata = extractMetadata(parser)
        if metadata is None:
            return {"error": "no metadata extracted"}

        if metadata.has("duration"):
            duration = metadata.get("duration")
            info["duration_seconds"] = duration.total_seconds()
        if metadata.has("width"):
            info["width"] = metadata.get("width")
        if metadata.has("height"):
            info["height"] = metadata.get("height")
        if metadata.has("creation_date"):
            info["date_recorded"] = str(metadata.get("creation_date"))
        for key in ("producer", "author", "comment"):
            if metadata.has(key):
                info.setdefault("device_info", str(metadata.get(key)))
    except Exception as exc:
        info["error"] = f"could not read video metadata: {exc}"

    return info or None
