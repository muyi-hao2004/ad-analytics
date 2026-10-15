from sqlalchemy import result_tuple

from my_package.greeting import say_hello

result = say_hello('Mia')
print(result)