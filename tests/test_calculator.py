def growth(old,new): return round((new-old)/old*100,2)
def margin(n,d): return round(n/d*100,2)
def test_alphabet(): assert growth(80539,90234)==12.04 and growth(11903,13556)==13.88
def test_margin(): assert margin(30606,90234)==33.92
