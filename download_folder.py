import shutil 
 
# 要压缩的文件夹路径
folder_path = '/home/aelab-1/411770448/Part1ClsTask-1' 
 
# 压缩后的文件路径 
zip_file_path = '/home/aelab-1/411770448/Part1ClsTask-1.zip' 
 
# 压缩文件夹
shutil.make_archive(zip_file_path.replace('.zip', ''), 'zip', folder_path)