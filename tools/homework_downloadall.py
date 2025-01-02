import os,sys
import multiprocessing

"""
download all homework from given student name, id, url description file

txt file format:
```
name, id, git rep url
```
"""

def homework_download(git_rep, dir_name):
    cmd = "git clone %s %s" % (git_rep, dir_name)
    print("exec command: %s" % cmd)
    os.system(cmd)

def homework_download_all(fname):
    pool = multiprocessing.Pool()

    lns = open(fname).readlines()
    for l in lns:
        items = l.split(",")
        if( len(items) >= 4 ):
            n = items[2].strip()
            id = items[1].strip()
            url = items[3].strip()

            if len(url) < 1: continue
        
            if not url.endswith(".git"):
                git_rep = url + ".git"
            else:
                git_rep = url
            dir_name = id + "_" + n

            print("%s : %s" % (dir_name, git_rep))
            print("")

            pool.apply_async(homework_download, (git_rep, dir_name))

    pool.close()
    pool.join()
    
if( __name__ == "__main__" ):
    if( len(sys.argv) > 1 ):
        fn = sys.argv[1]
        homework_download_all(fn)
    
