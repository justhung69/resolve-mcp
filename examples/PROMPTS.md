# Example prompts / Câu lệnh mẫu

Start every session with the bridge running (Workspace > Scripts > ClaudeBridge).

## Knowing the state / Nắm trạng thái
- "Check Resolve status, then summarize the open project: timelines, tracks, clips and render queue."
- "Take a full state snapshot. I'll edit something by hand, then tell me exactly what changed." *(get_full_state → diff_state)*
- "Kiểm tra trạng thái Resolve và tóm tắt project đang mở cho mình."
- "Chụp trạng thái project, mình sẽ sửa tay một chút, sau đó cho mình biết chính xác cái gì đã thay đổi."

## Media & timelines
- "Import everything in D:\\Footage\\Day1 into a new bin called Day 1, color the clips Orange and add a Green flag."
- "Make a timeline 'Rough cut' from Day 1 clips sorted by name, using only frames 24–120 of each clip."
- "Add a red marker named 'fix audio' at 00:00:12:00 on the current timeline."
- "Nhập tất cả file trong D:\\Footage\\Day1 vào bin mới tên 'Day 1', rồi dựng timeline 'Rough cut' từ các clip đó."

## Edits the API cannot do directly / Những thao tác API không có
- "Razor the current timeline at the playhead." *(timeline_razor → new timeline '(cut)')*
- "Trim the first clip by 10 frames from the end and close the gap (ripple), then slip the second clip 5 frames later."
  *(edit_list_get → edit_list_build)*
- "Rebuild this timeline with 12-frame cross dissolves between all clips on V1." *(timeline_build_otio)*
- "Make the third clip play at 200% speed." *(timeline_build_otio with speed)*
- "Cắt timeline tại vị trí playhead." / "Thêm hiệu ứng chuyển cảnh mờ dần 12 khung giữa các clip."

## Inspector & color
- "Zoom the selected clip to 120%, pan 80 px right and set opacity to 70%. Confirm the actual values."
- "Apply LUT 'Film Look.cube' to node 1 of every clip on V1, then show me which clips really got it."
- "Copy the grade from the first clip to all clips in the 'Interview' color group."
- "Export the grade of clip 3 as a 33-point LUT to D:\\LUTs\\look.cube."
- "Phóng to clip đang chọn lên 120% và giảm độ mờ còn 70%, rồi xác nhận giá trị thực tế."

## Fusion
- "Add a Fusion comp to the title clip, add a Blur tool with size 5 and tell me the tools in the comp."

## Render
- "Queue a render of 'Final' as H.264 MP4 1920x1080 to D:\\Exports named Final_v1 — don't start it."
- "Start the queued render job." *(asks for confirm=true)*
- "Thêm job render timeline 'Final' thành MP4 H.264 1080p vào D:\\Exports, chưa render vội."

## Safety
- "Delete the timeline 'Old test' — show me a dry run first."
- "What did you change in the last 20 operations?" *(op_log)*

## Beyond the API / Vượt giới hạn API
- "Give clips 1–5 a warm filmic look: lift shadows slightly blue, warm highlights, soft S-curve, slightly desaturate greens, and protect skin tones. Show me a preview frame." *(color_grade_lut + frame_preview)*
- "Brighten the subject in the left third of the frame with a soft circular window (gain master 1.3, softness 10), then show me the frame." *(color_grade_drx with window center_x/center_y + frame_preview)*
- "Làm sáng vùng chủ thể ở 1/3 bên trái khung bằng cửa sổ tròn mềm, rồi cho tôi xem ảnh kết quả." *(color_grade_drx + frame_preview)*
- "Slowly push in on the interview clip from 100% to 115% over its whole length, and fade it in over the first 12 frames." *(keyframe_transform)*
- "Duck the music clip by 10 dB between 00:00:05 and 00:00:12, fade it out over the last 2 seconds and normalise to -1 dBFS." *(audio_process)*
- "Làm tông ấm kiểu phim cho 5 clip đầu, giữ màu da tự nhiên, rồi cho mình xem ảnh thử."
- "Zoom chậm clip phỏng vấn từ 100% lên 115% suốt clip, và hiện dần (fade in) trong 12 khung đầu."
- "Giảm nhạc nền 10 dB từ giây 5 đến giây 12, fade out 2 giây cuối và chuẩn hóa về -1 dBFS."
